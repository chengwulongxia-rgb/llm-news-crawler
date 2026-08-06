"""Single article fetcher — extract article content from any URL.

Uses crawl4ai for both HTTP-only (fast) and browser-backed (JS-rendered)
content extraction. Produces clean markdown output automatically.
"""

from dataclasses import dataclass, field

# crawl4ai imports
import crawl4ai
from crawl4ai.async_configs import HTTPCrawlerConfig, CrawlerRunConfig, CacheMode, BrowserConfig
from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy

HAS_CRAWL4AI = True  # guaranteed by pyproject.toml


@dataclass
class FetchedArticle:
    """Structured article data extracted from a URL."""

    url: str
    title: str = ""
    author: str = ""
    date: str = ""
    body: str = ""
    site_name: str = ""
    method: str = "crawl4ai-http"  # "crawl4ai-http" or "crawl4ai-browser"

    def to_markdown(self) -> str:
        """Format as markdown for the editor pipeline."""
        parts = [f"# {self.title}", ""]
        if self.author:
            parts.append(f"**作者：** {self.author}")
        if self.date:
            parts.append(f"**日期：** {self.date}")
        if self.site_name:
            parts.append(f"**來源：** {self.site_name}")
        parts.extend(["", self.body, "", "---", f"*原文連結：{self.url}*"])
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "date": self.date,
            "body": self.body,
            "site_name": self.site_name,
            "method": self.method,
        }


# ── Shared crawler configs (reused across calls) ───────────────────

_HTTP_CFG = HTTPCrawlerConfig(
    headers={
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
    },
    follow_redirects=True,
    verify_ssl=True,
)

_BROWSER_CFG = BrowserConfig(
    headless=True,
    verbose=False,
)

_RUN_CFG = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    word_count_threshold=10,
    only_text=True,
)


def _extract_meta_from_result(result) -> dict:
    """Extract article metadata from crawl4ai CrawlResult."""
    meta = result.metadata or {}

    title = ""
    # crawl4ai metadata often has og:title or a top-level 'title'
    if isinstance(meta, dict):
        title = (
            meta.get("title", "")
            or meta.get("og:title", "")
            or ""
        )
        author = meta.get("author", "") or meta.get("article:author", "")
        date = (
            meta.get("article:published_time", "")
            or meta.get("date", "")
            or ""
        )
        site_name = meta.get("og:site_name", "")
    else:
        author = ""
        date = ""
        site_name = ""

    return {
        "title": title,
        "author": author,
        "date": date,
        "site_name": site_name,
    }


async def fetch_with_crawl4ai_http(
    url: str, timeout: int = 30
) -> FetchedArticle | None:
    """Fetch using crawl4ai HTTP-only mode (fast, no browser)."""
    try:
        strategy = AsyncHTTPCrawlerStrategy(
            browser_config=_HTTP_CFG,
        )
        async with crawl4ai.AsyncWebCrawler(
            crawler_strategy=strategy, always_bypass_cache=True
        ) as crawler:
            result = await crawler.arun(url, config=_RUN_CFG)

        if not result or not result.success:
            return None

        # Extract body from markdown
        md = result.markdown
        body = str(md.fit_markdown if md.fit_markdown else md)
        if len(body) < 100:
            return None

        meta = _extract_meta_from_result(result)
        title = meta["title"]
        # Fallback: try extracting from markdown heading
        if not title and body.startswith("# "):
            title = body.split("\n")[0].lstrip("# ")

        return FetchedArticle(
            url=result.url or url,
            title=title,
            author=meta["author"],
            date=meta["date"],
            body=body,
            site_name=meta["site_name"],
            method="crawl4ai-http",
        )

    except Exception:
        return None


async def fetch_with_crawl4ai_browser(
    url: str, timeout: int = 30, debug: bool = False
) -> FetchedArticle | None:
    """Fetch using crawl4ai browser mode (Playwright-backed, handles JS)."""
    import sys

    try:
        browser_cfg = BrowserConfig(
            headless=True,
            verbose=debug,
            extra_args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            word_count_threshold=10,
            only_text=True,
        )

        async with crawl4ai.AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url, config=run_cfg)

        if not result or not result.success:
            if debug:
                print(
                    f"[crawl4ai-browser] Failed: {result.error_message if result else 'None'}",
                    file=sys.stderr,
                )
            return None

        md = result.markdown
        body = str(md.fit_markdown if md.fit_markdown else md)
        if len(body) < 100:
            if debug:
                print(
                    f"[crawl4ai-browser] Body too short: {len(body)} chars",
                    file=sys.stderr,
                )
            return None

        meta = _extract_meta_from_result(result)
        title = meta["title"]
        if not title and body.startswith("# "):
            title = body.split("\n")[0].lstrip("# ")

        return FetchedArticle(
            url=result.url or url,
            title=title,
            author=meta["author"],
            date=meta["date"],
            body=body,
            site_name=meta["site_name"],
            method="crawl4ai-browser",
        )

    except Exception as e:
        if debug:
            print(
                f"[crawl4ai-browser] Exception: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
        return None


async def fetch_article(
    url: str,
    force_playwright: bool = False,
    timeout: int = 30,
    debug: bool = False,
) -> FetchedArticle | None:
    """Fetch a single article from a URL.

    Tries crawl4ai HTTP mode first (fast), falls back to browser mode (JS-rendered).

    Args:
        url: Article URL to fetch.
        force_playwright: Skip HTTP mode and use browser directly.
        timeout: Request timeout in seconds.
        debug: Print failure reasons to stderr.

    Returns:
        FetchedArticle or None if extraction failed.
    """
    import sys

    if not force_playwright:
        result = await fetch_with_crawl4ai_http(url, timeout)
        if result and len(result.body) > 100:
            return result
        if debug:
            print(
                "[fetch] crawl4ai HTTP failed, falling back to browser",
                file=sys.stderr,
            )

    # Fallback to browser mode
    result = await fetch_with_crawl4ai_browser(url, timeout, debug=debug)
    return result

"""Single article fetcher — extract article content from any URL.

Uses httpx for simple sites, Playwright for JS-rendered pages.
Extracts title, body text, and metadata.

Error Classification:
    When a fetch fails, call get_last_fetch_error() for:
      - PAYWALL    — hard paywall / login wall (WSJ, FT, Bloomberg)
      - CF_BLOCK   — Cloudflare / bot detection block
      - TIMEOUT    — request timed out
      - HTTP_ERROR — non-2xx status code
      - EMPTY      — page fetched but no usable content
      - UNKNOWN    — unexpected exception
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from enum import Enum, auto

import httpx
from bs4 import BeautifulSoup

# Playwright is optional (only needed for JS-rendered sites)
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# curl_cffi for TLS fingerprint impersonation (bypasses Cloudflare)
try:
    from curl_cffi import requests as curl_cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False


# ── Error classification ────────────────────────────────────────

class FetchError(Enum):
    """Classification of why a fetch failed."""
    PAYWALL = auto()      # Hard paywall / login wall
    CF_BLOCK = auto()     # Cloudflare or bot detection
    TIMEOUT = auto()      # Request timed out
    HTTP_ERROR = auto()   # Non-2xx HTTP status
    EMPTY = auto()        # Page fetched but no usable content
    UNKNOWN = auto()      # Unexpected error


_last_fetch_error: FetchError | None = None
_last_fetch_detail: str = ""


def get_last_fetch_error() -> tuple[FetchError | None, str]:
    """Return (error_type, detail_message) for the last failed fetch.
    
    Returns (None, "") if the last fetch succeeded.
    """
    return _last_fetch_error, _last_fetch_detail


def _set_fetch_error(err: FetchError, detail: str = ""):
    """Set the module-level fetch error state.

    PAYWALL is sticky — once detected, it won't be overwritten
    by less informative errors from subsequent fetch attempts.
    """
    global _last_fetch_error, _last_fetch_detail
    # Don't overwrite PAYWALL with less specific errors
    if _last_fetch_error == FetchError.PAYWALL and err != FetchError.PAYWALL:
        return
    _last_fetch_error = err
    _last_fetch_detail = detail


def _clear_fetch_error():
    """Clear the module-level fetch error state (call on success)."""
    global _last_fetch_error, _last_fetch_detail
    _last_fetch_error = None
    _last_fetch_detail = ""


# ── TLS fingerprint impersonation targets (tried in order) ─────

IMPERSONATE_TARGETS = [
    "chrome131",    # Latest Chrome
    "chrome124",    # Slightly older Chrome
    "safari18_0",   # Safari 18
    "firefox133",   # Firefox 133
]


@dataclass
class FetchedArticle:
    """Structured article data extracted from a URL."""
    url: str
    title: str = ""
    author: str = ""
    date: str = ""
    body: str = ""
    site_name: str = ""
    method: str = "httpx"  # "httpx" or "playwright"

    def to_markdown(self) -> str:
        """Format as markdown for the editor pipeline."""
        parts = [f"# {self.title}", ""]
        if self.author:
            parts.append(f"**作者：** {self.author}")
        if self.date:
            parts.append(f"**日期：** {self.date}")
        if self.site_name:
            parts.append(f"**來源：** {self.site_name}")
        parts.extend(["", self.body, "", f"---", f"*原文連結：{self.url}*"])
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


# ── Content Extraction ─────────────────────────────────────────────

# Elements to remove before extracting text
REMOVE_TAGS = [
    "script", "style", "nav", "footer", "header",
    "aside", "form", "iframe", "noscript",
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    ".sidebar", ".nav", ".footer", ".header", ".menu",
    ".comments", ".comment", "#comments",
    ".advertisement", ".ad", ".social-share", ".related-posts",
]

# Common article body selectors (tried in order)
BODY_SELECTORS = [
    "article",
    '[role="main"]',
    "main",
    ".post-content",
    ".article-content",
    ".entry-content",
    ".content",
    ".post",
    ".article",
    "#content",
    ".blog-post",
    ".markdown-body",
    ".prose",
    ".post-body",
    ".article-body",
    '[itemprop="articleBody"]',
]


def _clean_html(html: str) -> BeautifulSoup:
    """Parse HTML and remove noise elements."""
    soup = BeautifulSoup(html, "lxml")

    # Remove noise
    for tag in REMOVE_TAGS:
        for el in soup.select(tag):
            el.decompose()

    return soup


def _extract_title(soup: BeautifulSoup) -> str:
    """Extract article title."""
    # Try meta tags first
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or "").lower()
        name = (meta.get("name") or "").lower()
        if prop in ("og:title", "twitter:title") or name in ("twitter:title",):
            content = meta.get("content", "").strip()
            if content:
                return content

    # Try h1
    h1 = soup.find("h1")
    if h1:
        return h1.get_text().strip()

    # Try <title>
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        title = title_tag.string.strip()
        # Remove site name suffix
        title = re.sub(r"\s*[-–|\\]\s*.+$", "", title)
        return title

    return "(no title)"


def _extract_meta(soup: BeautifulSoup) -> dict:
    """Extract author and date from meta tags."""
    meta = {"author": "", "date": "", "site_name": ""}

    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or "").lower()
        name = (tag.get("name") or "").lower()
        content = tag.get("content", "").strip()

        if prop == "article:author" or name == "author":
            meta["author"] = content
        elif prop in ("article:published_time", "og:article:published_time") or name == "date":
            meta["date"] = content
        elif prop == "og:site_name":
            meta["site_name"] = content

    return meta


def _find_body_element(soup: BeautifulSoup) -> BeautifulSoup | None:
    """Find the main article body element."""
    for selector in BODY_SELECTORS:
        el = soup.select_one(selector)
        if el:
            # Check if this element has substantial text
            text = el.get_text().strip()
            if len(text) > 200:  # Must have at least 200 chars of content
                return el
    return None


def _body_to_text(body_el: BeautifulSoup) -> str:
    """Convert article body element to clean text."""
    # Get text with paragraph breaks
    paragraphs = []
    for tag in body_el.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre"]):
        text = tag.get_text().strip()
        if not text:
            continue

        if tag.name.startswith("h"):
            paragraphs.append(f"\n## {text}\n")
        elif tag.name == "blockquote":
            paragraphs.append(f"\n> {text}\n")
        elif tag.name == "li":
            paragraphs.append(f"• {text}")
        elif tag.name == "pre":
            code = tag.get_text()
            paragraphs.append(f"\n```\n{code}\n```\n")
        else:
            paragraphs.append(text)

    # If no structured paragraphs found, just get all text
    if not paragraphs:
        text = body_el.get_text()
        # Split on double newlines for paragraph breaks
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    return "\n\n".join(paragraphs)


# ── Paywall detection ────────────────────────────────────────────

# Signals that a page is behind a paywall or login wall
PAYWALL_SIGNALS = [
    "subscription required",
    "subscribe to continue",
    "please log in to continue",
    "sign in to read",
    "create an account to read",
    "you've reached your limit",
    "premium content",
    "this is a subscriber-only",
    "unlock this article",
    "already a subscriber?",
    "you need a subscription",
    "register to read",
    "paywall",
]


def _detect_paywall(html: str, status_code: int) -> bool:
    """Check if a page is behind a hard paywall."""
    if status_code == 403:
        return True
    if status_code == 401:
        return True
    html_lower = html.lower()
    if len(html) < 3000:
        # Very short page — could be paywall stub
        for signal in PAYWALL_SIGNALS:
            if signal in html_lower:
                return True
    return any(signal in html_lower for signal in PAYWALL_SIGNALS)


# ── Cloudflare-penetrating fetch (curl_cffi with TLS impersonation) ──

async def _try_curl_cffi_single(url: str, impersonate: str, timeout: int) -> tuple[str | None, int]:
    """Try a single curl_cffi request with a specific impersonation target.
    
    Returns (html, status_code) or (None, 0) on failure.
    """
    try:
        import asyncio as _asyncio

        def _sync_fetch():
            resp = curl_cffi_requests.get(
                url,
                impersonate=impersonate,
                timeout=timeout,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8",
                },
            )
            return resp

        resp = await _asyncio.to_thread(_sync_fetch)
        return resp.text, resp.status_code
    except Exception:
        return None, 0


async def fetch_with_curl_cffi(url: str, timeout: int = 30) -> FetchedArticle | None:
    """Fetch using curl_cffi with real browser TLS fingerprint impersonation.

    This is the ONLY method that reliably bypasses Cloudflare JS Challenge
    on sites like anthropic.com and openai.com. It mimics Chrome's TLS
    handshake exactly, which Cloudflare cannot distinguish from a real browser.
    
    Tries multiple impersonation targets in order (chrome131 → chrome124 → safari → firefox).
    """
    if not HAS_CURL_CFFI:
        return None

    for target in IMPERSONATE_TARGETS:
        try:
            html, status_code = await _try_curl_cffi_single(url, target, timeout)

            if html is None:
                continue

            if status_code >= 500:
                # Server error — try next target
                continue

            if status_code >= 400:
                # Check for paywall
                if _detect_paywall(html, status_code):
                    _set_fetch_error(FetchError.PAYWALL, f"HTTP {status_code}, paywall detected")
                    return None
                if status_code == 403:
                    _set_fetch_error(FetchError.HTTP_ERROR, f"HTTP 403 for {target}")
                else:
                    continue

            if not html or len(html) < 500:
                continue

            # Check for Cloudflare challenge page
            if "cf-browser-verification" in html.lower() or "checking your browser" in html.lower():
                _set_fetch_error(FetchError.CF_BLOCK, f"Cloudflare block with {target}")
                continue  # Try next target

            # Check for paywall in content
            if _detect_paywall(html, status_code):
                _set_fetch_error(FetchError.PAYWALL, "Paywall detected in page content")
                return None

            soup = _clean_html(html)
            title = _extract_title(soup)
            meta = _extract_meta(soup)
            body_el = _find_body_element(soup)

            if body_el is None:
                body_el = soup.find("body")
                if body_el is None:
                    _set_fetch_error(FetchError.EMPTY, "No body element found")
                    return None

            body_text = _body_to_text(body_el)

            if len(body_text) < 100:
                _set_fetch_error(FetchError.EMPTY, f"Body too short: {len(body_text)} chars")
                return None

            _clear_fetch_error()
            return FetchedArticle(
                url=url,
                title=title,
                author=meta["author"],
                date=meta["date"],
                body=body_text,
                site_name=meta["site_name"],
                method=f"curl_cffi/{target}",
            )

        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                _set_fetch_error(FetchError.TIMEOUT, str(e))
                continue
            # Other exceptions — try next target
            continue

    # All targets exhausted
    if _last_fetch_error is None:
        _set_fetch_error(FetchError.UNKNOWN, "All impersonation targets exhausted")
    return None


async def fetch_with_httpx(url: str, timeout: int = 20) -> FetchedArticle | None:
    """Fetch article content using httpx (works for static/SSR sites)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Linux"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "max-age=0",
            })
            resp.raise_for_status()

            html = resp.text
            if not html or len(html) < 100:
                _set_fetch_error(FetchError.EMPTY, f"HTML too short: {len(html) if html else 0} chars")
                return None

            # Check for paywall
            if _detect_paywall(html, resp.status_code):
                _set_fetch_error(FetchError.PAYWALL, "Paywall detected")
                return None

            soup = _clean_html(html)
            title = _extract_title(soup)
            meta = _extract_meta(soup)
            body_el = _find_body_element(soup)

            if body_el is None:
                # Fallback: try the whole body minus nav/footer
                body_el = soup.find("body")
                if body_el is None:
                    _set_fetch_error(FetchError.EMPTY, "No body element found")
                    return None

            body_text = _body_to_text(body_el)

            if len(body_text) < 100:
                _set_fetch_error(FetchError.EMPTY, f"Body too short: {len(body_text)} chars")
                return None

            _clear_fetch_error()
            return FetchedArticle(
                url=url,
                title=title,
                author=meta["author"],
                date=meta["date"],
                body=body_text,
                site_name=meta["site_name"],
                method="httpx",
            )

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 403:
            _set_fetch_error(FetchError.CF_BLOCK, f"HTTP 403")
        elif status == 401:
            _set_fetch_error(FetchError.PAYWALL, f"HTTP 401")
        else:
            _set_fetch_error(FetchError.HTTP_ERROR, f"HTTP {status}")
        return None
    except httpx.TimeoutException:
        _set_fetch_error(FetchError.TIMEOUT, "httpx timeout")
        return None
    except Exception as e:
        _set_fetch_error(FetchError.UNKNOWN, f"{type(e).__name__}: {e}")
        return None


async def fetch_with_playwright(url: str, timeout: int = 30, debug: bool = False) -> FetchedArticle | None:
    """Fetch article content using Playwright (for JS-rendered sites)."""
    if not HAS_PLAYWRIGHT:
        if debug:
            print(f"[playwright] Not installed", file=__import__('sys').stderr)
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            # Use default context — custom contexts can break stealth and trigger bot detection
            page = await browser.new_page()

            # Apply comprehensive stealth patches via playwright-stealth
            try:
                from playwright_stealth import stealth_async
                await stealth_async(page)
            except ImportError:
                pass

            # Additional stealth scripts to hide automation fingerprints
            await page.add_init_script("""
                // Overwrite navigator.webdriver
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                // Overwrite chrome.runtime (missing in headless = bot signal)
                window.chrome = { runtime: {} };
                // Overwrite permissions.query for notifications (bot signal)
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(parameters)
                );
                // Overwrite plugins (empty = bot signal)
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                // Overwrite languages
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'zh-TW', 'zh'] });
            """)

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            # Wait for JS-rendered content to settle (but don't wait for never-ending CF challenges)
            await page.wait_for_timeout(3000)

            # Check for Cloudflare challenge page
            page_title = await page.title()
            content = await page.content()
            cloudflare_signals = [
                "cf-browser-verification",
                "checking your browser",
                "this page couldn",
                "please enable javascript",
                "attention required",
                "cloudflare",
            ]
            is_blocked = (
                len(content) < 5000
                or any(signal in content.lower() for signal in cloudflare_signals)
                or any(signal in page_title.lower() for signal in cloudflare_signals)
            )
            if is_blocked:
                if debug:
                    print(f"[playwright] Likely blocked by Cloudflare (title='{page_title}', html={len(content)} bytes)", file=__import__('sys').stderr)
                _set_fetch_error(FetchError.CF_BLOCK, f"Cloudflare block (title='{page_title}')")
                await browser.close()
                return None

            # Check for paywall in rendered content
            if _detect_paywall(content, 200):
                _set_fetch_error(FetchError.PAYWALL, "Paywall detected in rendered page")
                await browser.close()
                return None

            soup = _clean_html(content)
            title = _extract_title(soup)
            meta = _extract_meta(soup)
            body_el = _find_body_element(soup)

            if body_el is None:
                body_el = soup.find("body")

            if body_el is None:
                if debug:
                    print(f"[playwright] No body element found", file=__import__('sys').stderr)
                _set_fetch_error(FetchError.EMPTY, "No body element found")
                await browser.close()
                return None

            body_text = _body_to_text(body_el)

            if len(body_text) < 100:
                if debug:
                    print(f"[playwright] Body too short: {len(body_text)} chars", file=__import__('sys').stderr)
                _set_fetch_error(FetchError.EMPTY, f"Body too short: {len(body_text)} chars")
                await browser.close()
                return None

            await browser.close()

            _clear_fetch_error()
            return FetchedArticle(
                url=url,
                title=title,
                author=meta["author"],
                date=meta["date"],
                body=body_text,
                site_name=meta["site_name"],
                method="playwright",
            )

    except Exception as e:
        error_str = str(e).lower()
        if "timeout" in error_str or "timed out" in error_str:
            _set_fetch_error(FetchError.TIMEOUT, str(e))
        elif "net::err" in error_str:
            _set_fetch_error(FetchError.HTTP_ERROR, str(e))
        else:
            _set_fetch_error(FetchError.UNKNOWN, f"{type(e).__name__}: {e}")
        if debug:
            print(f"[playwright] Exception: {type(e).__name__}: {e}", file=__import__('sys').stderr)
        return None


async def fetch_article(
    url: str,
    force_playwright: bool = False,
    timeout: int = 30,
    debug: bool = False,
) -> FetchedArticle | None:
    """Fetch a single article from a URL.

    Tries curl_cffi first (with multi-target TLS impersonation),
    then httpx, then Playwright.

    Args:
        url: Article URL to fetch.
        force_playwright: Skip httpx and use Playwright directly.
        timeout: Request timeout in seconds.
        debug: Print failure reasons to stderr.

    Returns:
        FetchedArticle or None if extraction failed.
        Check get_last_fetch_error() for failure reason.
    """
    if not force_playwright:
        # Try curl_cffi first (TLS impersonation — penetrates Cloudflare)
        if HAS_CURL_CFFI:
            result = await fetch_with_curl_cffi(url, timeout)
            if result and len(result.body) > 200:
                return result
            if debug:
                err, detail = get_last_fetch_error()
                print(f"[fetch] curl_cffi failed ({err.name if err else 'unknown'}: {detail}), falling back to httpx", file=__import__('sys').stderr)

        # Try httpx with browser headers
        result = await fetch_with_httpx(url, timeout)
        if result and len(result.body) > 200:
            return result
        if debug:
            err, detail = get_last_fetch_error()
            print(f"[fetch] httpx failed ({err.name if err else 'unknown'}: {detail}), falling back to playwright", file=__import__('sys').stderr)

    # Fallback to Playwright
    if HAS_PLAYWRIGHT:
        return await fetch_with_playwright(url, timeout, debug=debug)

    return None

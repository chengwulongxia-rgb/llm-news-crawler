"""Sitemap-based blog crawler for Anthropic and Mistral.

These sites are JS-rendered but their sitemaps are static XML.
We extract article URLs from sitemaps, fetch each page, and parse the <title> tag.
"""

import asyncio
import re
from datetime import datetime, timezone
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from crawler.models import NewsItem

# ── Sitemap Source Definitions ──────────────────────────────────────

SITEMAP_SOURCES = [
    {
        "name": "Anthropic",
        "sitemap_url": "https://www.anthropic.com/sitemap.xml",
        "url_filters": ["/blog/", "/research/", "/engineering/"],
        "url_excludes": ["tag/", "category/", "author/", "page/"],
        "score": 35,
    },
    {
        "name": "Mistral",
        "sitemap_url": "https://mistral.ai/sitemap-0.xml",
        "url_filters": ["/news/"],
        "url_excludes": ["/fr/", "/it/", r"/news/?$"],  # Skip non-English + index page
        "score": 30,
    },
]


def _parse_sitemap(xml_text: str, source_config: dict) -> list[str]:
    """Extract article URLs from a sitemap XML.

    Filters URLs based on url_filters (inclusion) and url_excludes (exclusion).
    url_excludes can contain regular expressions for precise matching.
    """
    filters = source_config.get("url_filters", [])
    excludes = source_config.get("url_excludes", [])

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    # Namespace handling
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = root.findall(".//sm:url", ns)
    if not urls:
        urls = root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}url")
    if not urls:
        urls = root.findall(".//url")

    results = []
    for url_elem in urls:
        loc_elem = url_elem.find("sm:loc", ns)
        if loc_elem is None:
            loc_elem = url_elem.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        if loc_elem is None:
            loc_elem = url_elem.find("loc")

        if loc_elem is None or not loc_elem.text:
            continue

        href = loc_elem.text.strip()

        # Apply URL filters (must match at least one)
        if filters and not any(f in href for f in filters):
            continue

        # Apply URL excludes (skip if any exclude pattern matches)
        if excludes:
            excluded = False
            for exc in excludes:
                if re.search(exc, href):
                    excluded = True
                    break
            if excluded:
                continue

        results.append(href)

    return results


def _extract_title_from_html(html: str, source_name: str = "") -> str:
    """Extract article title from HTML <title> tag, stripping site name."""
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        title = title_tag.string.strip()
        # Remove common site name suffixes
        title = re.sub(r"\s*[-–|\\]\s*(Anthropic|Mistral AI|Mistral|OpenAI|Google AI).*$", "", title, flags=re.IGNORECASE)
        return title.strip()
    return "(no title)"


def _parse_pub_date_from_html(html: str) -> datetime | None:
    """Try to extract publication date from HTML meta tags."""
    soup = BeautifulSoup(html, "lxml")
    # Try meta tags
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "") or meta.get("name", "")
        if prop in ("article:published_time", "date", "pubdate"):
            content = meta.get("content", "")
            if content:
                try:
                    return datetime.fromisoformat(content.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
    return None


async def _fetch_page_title(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str, datetime | None]:
    """Fetch a page and extract its title and date. Returns (url, title, date)."""
    async with semaphore:
        try:
            resp = await client.get(url, timeout=15.0, follow_redirects=True)
            resp.raise_for_status()
            title = _extract_title_from_html(resp.text)
            pub_date = _parse_pub_date_from_html(resp.text)
            return url, title, pub_date
        except Exception:
            return url, "(no title)", None


async def fetch_sitemap_source(
    client: httpx.AsyncClient,
    source_config: dict,
    limit: int = 10,
    concurrency: int = 5,
) -> list[NewsItem]:
    """Fetch articles from a sitemap-based blog source.

    Args:
        client: Shared httpx.AsyncClient.
        source_config: Source definition dict.
        limit: Max articles to return.
        concurrency: Max concurrent page fetches.

    Returns:
        List of NewsItem.
    """
    source_name = source_config["name"]
    sitemap_url = source_config["sitemap_url"]
    score = source_config.get("score", 30)

    # Fetch and parse sitemap
    try:
        resp = await client.get(sitemap_url, timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        article_urls = _parse_sitemap(resp.text, source_config)
    except Exception:
        return []

    if not article_urls:
        return []

    # Limit URLs to fetch (pick most recent-looking — sitemaps are usually last-modified order)
    article_urls = article_urls[: limit * 2]  # Fetch extra in case some fail

    # Fetch pages concurrently
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [_fetch_page_title(client, url, semaphore) for url in article_urls]
    results = await asyncio.gather(*tasks)

    # Build NewsItems
    items = []
    seen_titles = set()
    for url, title, pub_date in results:
        if title == "(no title)" or title in seen_titles:
            continue
        seen_titles.add(title)

        items.append(NewsItem(
            title=title,
            url=url,
            source=source_name,
            score=score,
            published_at=pub_date,
        ))

        if len(items) >= limit:
            break

    return items


async def fetch_all_sitemaps(
    client: httpx.AsyncClient | None = None,
    limit_per_source: int = 5,
) -> list[NewsItem]:
    """Fetch all configured sitemap sources concurrently."""
    close_client = False
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True)
        close_client = True

    try:
        tasks = [fetch_sitemap_source(client, src, limit_per_source) for src in SITEMAP_SOURCES]
        results = await asyncio.gather(*tasks)
        all_items = []
        for result in results:
            all_items.extend(result)
        return all_items
    finally:
        if close_client:
            await client.aclose()

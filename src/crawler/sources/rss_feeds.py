"""RSS/Atom feed crawler for company blogs and research sources.

Uses stdlib xml.etree.ElementTree — no extra deps for RSS parsing.
"""

import asyncio
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from crawler.models import NewsItem

# ── Source Definitions ──────────────────────────────────────────────

RSS_SOURCES = [
    {
        "name": "OpenAI",
        "url": "https://openai.com/blog/rss.xml",
        "item_tag": "item",
        "title_tag": "title",
        "link_tag": "link",
        "desc_tag": "description",
        "date_tag": "pubDate",
        "date_format": "rfc2822",
        "strip_cdata": True,
    },
    {
        "name": "Google AI Blog",
        "url": "https://blog.google/technology/ai/rss/",
        "item_tag": "item",  # RSS 2.0 format (not Atom)
        "title_tag": "title",
        "link_tag": "link",
        "desc_tag": "description",
        "date_tag": "pubDate",
        "date_format": "rfc2822",
        "strip_cdata": True,
    },
    {
        "name": "Google Research",
        "url": "https://blog.research.google/feeds/posts/default",
        "item_tag": "entry",
        "title_tag": "title",
        "link_tag": "link",
        "desc_tag": "summary",
        "date_tag": "published",
        "date_format": "iso",
        "strip_cdata": False,
    },
    {
        "name": "ArXiv-CL",
        "url": "https://arxiv.org/rss/cs.CL",
        "item_tag": "item",
        "title_tag": "title",
        "link_tag": "link",
        "desc_tag": "description",
        "date_tag": "pubDate",
        "date_format": "rfc2822",
        "strip_cdata": False,
    },
    {
        "name": "ArXiv-AI",
        "url": "https://arxiv.org/rss/cs.AI",
        "item_tag": "item",
        "title_tag": "title",
        "link_tag": "link",
        "desc_tag": "description",
        "date_tag": "pubDate",
        "date_format": "rfc2822",
        "strip_cdata": False,
    },
]

# Namespaces for Atom feeds
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _clean_html(text: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    if not text:
        return ""
    # Remove CDATA wrapper if present
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#8217;", "'").replace("&#8216;", "'")
    text = text.replace("&#8211;", "–").replace("&#8212;", "—")
    text = re.sub(r"&#\d+;", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_first_sentence(text: str, max_chars: int = 200) -> str:
    """Extract a short summary from description text."""
    text = _clean_html(text)
    if not text:
        return ""
    # Take first sentence or first N chars
    match = re.match(r"([^。.!?]+[。.!?])", text)
    if match:
        summary = match.group(1)
    else:
        summary = text[:max_chars]
    if len(summary) > max_chars:
        summary = summary[:max_chars - 3] + "..."
    return summary


def _parse_rss_date(date_str: str, date_format: str) -> datetime | None:
    """Parse date string based on format hint."""
    if not date_str:
        return None
    try:
        if date_format == "rfc2822":
            return parsedate_to_datetime(date_str)
        elif date_format == "iso":
            # Handle ISO 8601 with timezone
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    return None


def _extract_best_link(item_elem: ElementTree.Element, link_tag: str) -> str:
    """Extract the best URL from an RSS/Atom item element.

    Handles Blogger Atom feeds which have multiple <link> elements:
    - <link rel="alternate" type="text/html" href="...">  ← we want this
    - <link rel="replies" ...>  (comment feed)
    - <link href="...">  (default, often comment feed)
    """
    all_links = item_elem.findall(link_tag)
    if not all_links:
        all_links = item_elem.findall(f"{{*}}{link_tag}")

    if not all_links:
        return ""

    # Prefer rel="alternate" with type="text/html"
    for link in all_links:
        rel = link.get("rel", "")
        link_type = link.get("type", "")
        href = link.get("href", "")
        if rel == "alternate" and "html" in link_type and href:
            return href

    # Then any rel="alternate"
    for link in all_links:
        rel = link.get("rel", "")
        href = link.get("href", "")
        if rel == "alternate" and href:
            return href

    # Then the last link with href (first is often comment feed in Blogger)
    found_href = ""
    for link in all_links:
        href = link.get("href", "")
        if href:
            found_href = href

    if found_href:
        return found_href

    # RSS-style: text content
    for link in all_links:
        if link.text and link.text.strip():
            return link.text.strip()

    return ""


def parse_rss_feed(xml_text: str, source_config: dict, limit: int = 20) -> list[NewsItem]:
    """Parse an RSS/Atom XML feed into NewsItem list.

    Args:
        xml_text: Raw XML response body.
        source_config: Source definition dict from RSS_SOURCES.
        limit: Max items to return.

    Returns:
        List of NewsItem objects.
    """
    source_name = source_config["name"]
    item_tag = source_config["item_tag"]
    title_tag = source_config["title_tag"]
    link_tag = source_config["link_tag"]
    desc_tag = source_config["desc_tag"]
    date_tag = source_config["date_tag"]
    date_format = source_config["date_format"]
    strip_cdata = source_config["strip_cdata"]

    # Clean the XML text before parsing
    xml_text = xml_text.strip()
    if strip_cdata and "<![CDATA[" in xml_text:
        xml_text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: m.group(1).replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;"), xml_text, flags=re.DOTALL)

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    # Find items - try both with and without namespace
    items = root.findall(f".//{item_tag}")
    if not items:
        items = root.findall(f".//{{*}}{item_tag}")
    if not items:
        items = root.findall(f".//atom:{item_tag}", ATOM_NS)

    results = []
    for item in items[:limit]:
        # Title
        title_elem = item.find(title_tag)
        if title_elem is None:
            title_elem = item.find(f"{{*}}{title_tag}")
        title = _clean_html(title_elem.text) if title_elem is not None and title_elem.text else "(no title)"

        # Link — handle multiple link elements in Atom feeds
        url = _extract_best_link(item, link_tag)

        # Description/summary
        desc_elem = item.find(desc_tag)
        if desc_elem is None:
            desc_elem = item.find(f"{{*}}{desc_tag}")
        description = desc_elem.text if desc_elem is not None and desc_elem.text else ""

        # Date
        date_elem = item.find(date_tag)
        if date_elem is None:
            date_elem = item.find(f"{{*}}{date_tag}")
        date_str = date_elem.text if date_elem is not None and date_elem.text else ""
        published_at = _parse_rss_date(date_str, date_format)

        # Build summary
        summary = _extract_first_sentence(description)

        results.append(NewsItem(
            title=title,
            url=url,
            source=source_name,
            score=30,  # Official blog post, default higher score
            summary=summary,
            published_at=published_at,
        ))

    return results


async def fetch_rss_source(
    client: httpx.AsyncClient,
    source_config: dict,
    limit: int = 20,
) -> list[NewsItem]:
    """Fetch and parse a single RSS feed source."""
    url = source_config["url"]
    try:
        resp = await client.get(url, timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        return parse_rss_feed(resp.text, source_config, limit)
    except Exception:
        return []


async def fetch_all_rss(
    client: httpx.AsyncClient | None = None,
    limit_per_source: int = 10,
) -> list[NewsItem]:
    """Fetch all configured RSS sources concurrently.

    Args:
        client: Optional shared httpx.AsyncClient.
        limit_per_source: Max items per source.

    Returns:
        Combined list of NewsItem from all RSS sources.
    """
    close_client = False
    if client is None:
        client = httpx.AsyncClient()
        close_client = True

    try:
        tasks = [fetch_rss_source(client, src, limit_per_source) for src in RSS_SOURCES]
        results = await asyncio.gather(*tasks)
        all_items = []
        for result in results:
            all_items.extend(result)
        return all_items
    finally:
        if close_client:
            await client.aclose()

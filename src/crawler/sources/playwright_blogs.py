"""Playwright-based blog crawler for JS-rendered company blogs.

Uses headless Chromium to navigate listing pages, wait for rendering,
and extract article metadata (title, description, date, URL).
Replaces the sitemap-based approach for richer data extraction.
"""

import asyncio
import re
from datetime import datetime

from playwright.async_api import async_playwright, Browser, Page

from crawler.models import NewsItem

# ── Blog Source Definitions ─────────────────────────────────────────

PLAYWRIGHT_SOURCES = [
    {
        "name": "Anthropic",
        "list_url": "https://www.anthropic.com/news",
        "article_selector": "a[href^='/news/']",
        "title_selector": "h2, h3, [class*='title'], [class*='heading']",
        "desc_selector": "p, [class*='description'], [class*='excerpt']",
        "date_selector": "time, [class*='date'], [datetime]",
        "base_url": "https://www.anthropic.com",
        "score": 35,
        "max_articles": 10,
    },
    {
        "name": "Mistral",
        "list_url": "https://mistral.ai/news/",
        "article_selector": "a[href^='/news/']",
        "title_selector": "h2, h3, [class*='title'], [class*='heading']",
        "desc_selector": "p, [class*='description'], [class*='excerpt']",
        "date_selector": "time, [class*='date'], span",
        "base_url": "https://mistral.ai",
        "score": 30,
        "max_articles": 10,
    },
]

# URL patterns to skip (non-article pages)
SKIP_URL_PATTERNS = [
    r"/news/?$",       # Index pages
    r"/blog/?$",
    r"/tag/",
    r"/category/",
    r"/author/",
    r"/page/",
    r"/fr/", r"/it/",  # Non-English
]


def _is_article_url(url: str) -> bool:
    """Check if a URL looks like an article (not index/tag/category page)."""
    for pattern in SKIP_URL_PATTERNS:
        if re.search(pattern, url):
            return False
    return True


def _clean_text(text: str) -> str:
    """Clean extracted text."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_article_card_text(raw_text: str) -> dict:
    """Parse raw text from an article card/link into structured fields.

    Handles patterns like:
    - Anthropic: "Title\\nCategory\\nDate\\n\\nDescription"
    - Mistral: "SECTION Title text..."
    - Simple: just the title

    Returns dict with keys: title, category, date_str, description
    """
    text = raw_text.strip()
    if not text:
        return {"title": "(no title)", "category": "", "date_str": "", "description": ""}

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Pattern: Mistral-style with section prefix
    section_prefixes = [
        "SOLUTIONS", "RESEARCH", "COMPANY", "PRODUCT", "ANNOUNCEMENTS",
        "POLICY", "TECHNICAL", "CULTURE", "ENGINEERING", "NEWS",
    ]
    category = ""
    for prefix in section_prefixes:
        if lines and lines[0].upper().startswith(prefix.upper()):
            # Check if the rest of the first line has actual title
            rest = lines[0][len(prefix):].strip()
            if rest:
                category = lines[0].split()[0] if lines[0].split() else ""
                lines[0] = rest  # Replace first line with just the title part
            break

    # Try to identify date line (short, has month name or numbers)
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
        "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    date_str = ""
    date_idx = -1
    for i, line in enumerate(lines):
        if any(m in line for m in month_names) and len(line) < 30:
            date_str = line
            date_idx = i
            break
        # Also match "Jun 3, 2026" style at start
        if re.match(r"^[A-Z][a-z]{2,8}\s+\d{1,2},?\s*\d{4}", line):
            date_str = line
            date_idx = i
            break
        # Or just a line that looks like a standalone date
        if re.match(r"^\d{4}-\d{2}-\d{2}$", line):
            date_str = line
            date_idx = i
            break

    # Category is often a short uppercase line before or after the date
    if date_idx >= 0 and not category:
        # Check line before/after date for category
        if date_idx > 0 and len(lines[date_idx - 1]) < 20 and lines[date_idx - 1].isupper():
            category = lines[date_idx - 1]
        elif date_idx + 1 < len(lines) and len(lines[date_idx + 1]) < 25 and lines[date_idx + 1].isupper():
            category = lines[date_idx + 1]
        elif date_idx > 0 and len(lines[date_idx - 1]) < 25:
            category = lines[date_idx - 1]

    # Title: first non-date, non-category line
    title = "(no title)"
    for i, line in enumerate(lines):
        if i == date_idx:
            continue
        # Skip short category-like lines
        if len(line) < 20 and line.isupper():
            continue
        if line and line not in (category, date_str):
            title = line
            break

    # Description: remaining text after title
    desc_lines = []
    found_title = False
    for line in lines:
        if line == title and not found_title:
            found_title = True
            continue
        if line in (category, date_str):
            continue
        if found_title:
            desc_lines.append(line)

    # If title is too long (whole card text leaked), take first meaningful sentence
    if len(title) > 120:
        # Truncate at first sentence boundary
        match = re.match(r"^(.{10,120}?)[.!?]\s", title)
        if match:
            title = match.group(1) + "."
            desc_lines.insert(0, title[match.end():])

    description = " ".join(desc_lines).strip()[:300]

    return {
        "title": title,
        "category": category,
        "date_str": date_str,
        "description": description,
    }


def _parse_relative_date(text: str) -> datetime | None:
    """Try to parse a date string, including relative dates from time elements."""
    if not text:
        return None

    # Try ISO format first (datetime attribute or text)
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    ]:
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue

    return None


async def _extract_articles_from_page(
    page: Page,
    source_config: dict,
    max_articles: int = 10,
) -> list[NewsItem]:
    """Extract article metadata from a fully rendered blog listing page.

    Strategy: find all article links, then for each link element, look for
    nearby title, description, and date elements.
    """
    source_name = source_config["name"]
    article_sel = source_config["article_selector"]
    base_url = source_config["base_url"]
    score = source_config.get("score", 30)

    # Get all article link elements
    link_elements = await page.query_selector_all(article_sel)

    items = []
    seen_urls = set()

    for link_el in link_elements:
        if len(items) >= max_articles:
            break

        href = await link_el.get_attribute("href")
        if not href:
            continue

        # Build full URL
        if href.startswith("http"):
            url = href
        else:
            url = base_url.rstrip("/") + "/" + href.lstrip("/")

        if not _is_article_url(url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Extract text from the link element itself (often contains title)
        raw_text = await link_el.inner_text()
        parsed = _parse_article_card_text(raw_text)
        title = parsed["title"]
        description = parsed["description"]
        date_str = parsed["date_str"]

        # Parse date from extracted date string
        published_at = None
        if date_str:
            published_at = _parse_relative_date(date_str)

        items.append(NewsItem(
            title=title,
            url=url,
            source=source_name,
            score=score,
            summary=description[:300] if description else "",
            published_at=published_at,
        ))

    return items


async def fetch_playwright_source(
    browser: Browser,
    source_config: dict,
    max_articles: int = 10,
) -> list[NewsItem]:
    """Fetch articles from a JS-rendered blog using Playwright.

    Args:
        browser: Shared Playwright Browser instance.
        source_config: Source definition dict.
        max_articles: Max articles to extract.

    Returns:
        List of NewsItem.
    """
    source_name = source_config["name"]
    list_url = source_config["list_url"]

    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = await context.new_page()

    try:
        # Navigate and wait for content
        await page.goto(list_url, wait_until="networkidle", timeout=30000)

        # Wait for article elements to appear
        article_sel = source_config["article_selector"]
        try:
            await page.wait_for_selector(article_sel, timeout=10000)
        except Exception:
            # If no articles found, try waiting a bit more
            await asyncio.sleep(3)

        # Extract articles
        items = await _extract_articles_from_page(page, source_config, max_articles)

        return items

    except Exception as e:
        # Fail gracefully — return empty list
        return []

    finally:
        await context.close()


async def fetch_all_playwright(
    limit_per_source: int = 10,
) -> list[NewsItem]:
    """Fetch all Playwright-based blog sources.

    Shares a single browser instance across all sources for efficiency.

    Args:
        limit_per_source: Max articles per source.

    Returns:
        Combined list of NewsItem from all Playwright sources.
    """
    all_items = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        try:
            for source_config in PLAYWRIGHT_SOURCES:
                max_articles = source_config.get("max_articles", limit_per_source)
                items = await fetch_playwright_source(browser, source_config, max_articles)
                all_items.extend(items)

            return all_items

        finally:
            await browser.close()

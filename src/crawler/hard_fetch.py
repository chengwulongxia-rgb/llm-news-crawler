"""
Hard-content fetchers for specific site types.

These go beyond generic article_fetcher by targeting
specific HTML structures on common LLM news sources.

Supported fetchers:
- fetch_arxiv_abstract(url)      → extract paper abstract + metadata
- fetch_hackernews_thread(url)   → extract OP + top comments
- fetch_youtube_transcript(url)  → extract video transcript
- fetch_generic(url)             → generic article content
"""

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


@dataclass
class HardFetchedContent:
    url: str
    title: str = ""
    body: str = ""       # main text content
    meta: dict = None    # extra metadata (author, date, etc.)

    def __post_init__(self):
        if self.meta is None:
            self.meta = {}

    def to_markdown(self) -> str:
        parts = [f"# {self.title}", ""]
        if self.meta.get("author"):
            parts.append(f"**作者：** {self.meta['author']}")
        if self.meta.get("date"):
            parts.append(f"**日期：** {self.meta['date']}")
        if self.meta.get("site_name"):
            parts.append(f"**來源：** {self.meta['site_name']}")
        parts.extend(["", self.body, "", "---", f"*原文連結：{self.url}*"])
        return "\n".join(parts)


# ── arXiv abstract fetcher ────────────────────────────────────────

ARXIV_PATTERN = re.compile(r"arxiv\.org/abs/([\d.]+)")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LLMCrawler/1.0; +https://chengwulongxia-rgb.github.io)",
    "Accept": "text/html,application/xhtml+xml",
}


async def fetch_arxiv_abstract(url: str, timeout: int = 15) -> HardFetchedContent | None:
    """Extract title + abstract from an arXiv paper page.

    Targets the <blockquote class="abstract"> element directly,
    which is faster and cleaner than generic article extraction.
    """
    match = ARXIV_PATTERN.search(url)
    paper_id = match.group(1) if match else "unknown"

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

        # Title
        title_tag = soup.find("h1", class_="title")
        title = title_tag.get_text().strip() if title_tag else "(no title)"
        # Clean "Title:" prefix
        title = re.sub(r"^Title:\s*", "", title)

        # Abstract
        abstract_block = soup.find("blockquote", class_="abstract")
        if abstract_block:
            # Remove the "Abstract:" label
            abstract_text = abstract_block.get_text().strip()
            abstract_text = re.sub(r"^Abstract:\s*", "", abstract_text)
        else:
            abstract_text = "(abstract not found)"

        # Authors
        authors_div = soup.find("div", class_="authors")
        authors = ""
        if authors_div:
            authors = authors_div.get_text().strip()
            authors = re.sub(r"^Authors:\s*", "", authors)

        body = abstract_text

        return HardFetchedContent(
            url=url,
            title=title,
            body=body,
            meta={
                "author": authors,
                "paper_id": paper_id,
                "site_name": "arXiv",
            },
        )

    except Exception:
        return None


# ── Hacker News thread fetcher ─────────────────────────────────────

HN_ITEM_PATTERN = re.compile(r"news\.ycombinator\.com/item\?id=(\d+)")

HN_API = "https://hacker-news.firebaseio.com/v0"


async def fetch_hackernews_thread(url: str, max_comments: int = 6, timeout: int = 15) -> HardFetchedContent | None:
    """Fetch a Hacker News Ask HN / discussion thread.

    Extracts the original post text + top-level comments.
    Uses the HN Firebase API for structured data when available,
    falls back to HTML scraping.
    """
    match = HN_ITEM_PATTERN.search(url)
    if not match:
        return None

    item_id = match.group(1)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            # Try Firebase API first
            api_url = f"{HN_API}/item/{item_id}.json"
            resp = await client.get(api_url, headers=HEADERS)

            if resp.status_code == 200:
                data = resp.json()
                return _parse_hn_api(data, item_id, max_comments, url)

            # Fallback: scrape HTML
            resp2 = await client.get(url, headers=HEADERS)
            resp2.raise_for_status()
            return _parse_hn_html(resp2.text, item_id, url)

    except Exception:
        # Last resort: scrape HTML
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                resp = await client.get(url, headers=HEADERS)
                resp.raise_for_status()
                return _parse_hn_html(resp.text, item_id, url)
        except Exception:
            return None


def _parse_hn_api(data: dict, item_id: str, max_comments: int, url: str) -> HardFetchedContent:
    """Parse HN Firebase API response."""
    title = data.get("title", "Ask HN")
    body_parts = []

    # Original post text
    if data.get("text"):
        body_parts.append(data["text"])
        body_parts.append("")

    # Author info
    author = data.get("by", "")

    # Top-level comments
    kids = data.get("kids", [])[:max_comments * 2]  # Fetch more to account for nesting
    body_parts.append("---")
    body_parts.append("")

    comment_count = 0
    for kid_id in kids:
        if comment_count >= max_comments:
            break
        comment = _fetch_hn_comment_sync(kid_id)
        if comment:
            body_parts.append(comment)
            comment_count += 1

    return HardFetchedContent(
        url=url,
        title=title,
        body="\n\n".join(body_parts),
        meta={
            "author": author,
            "site_name": "Hacker News",
            "item_id": item_id,
        },
    )


def _fetch_hn_comment_sync(comment_id: int) -> str | None:
    """Fetch a single HN comment (synchronous, for use in parse loop)."""
    import json
    import html as _html
    from urllib.request import urlopen

    try:
        resp = urlopen(f"{HN_API}/item/{comment_id}.json", timeout=10)
        data = json.loads(resp.read())
        if not data or data.get("deleted") or data.get("dead"):
            return None

        author = data.get("by", "anonymous")
        text = _html.unescape(data.get("text", ""))
        if not text or len(text) < 50:
            return None

        # Format as markdown
        lines = [f"### {author}", "", text]
        return "\n".join(lines)

    except Exception:
        return None


def _parse_hn_html(html: str, item_id: str, url: str) -> HardFetchedContent | None:
    """Fallback HTML scraper for HN pages."""
    soup = BeautifulSoup(html, "lxml")

    # Title
    title_tag = soup.find("title")
    title = title_tag.get_text().strip() if title_tag else "Ask HN"
    title = title.replace(" | Hacker News", "")

    # Top post text
    top_post = soup.select_one(".toptext")
    body_parts = []
    if top_post:
        body_parts.append(top_post.get_text().strip())

    # Comments
    comments = soup.select(".commtext")[:6]
    if comments:
        body_parts.append("\n---\n")
        for i, c in enumerate(comments):
            text = c.get_text().strip()
            if len(text) > 50:
                # Try to get author
                author_link = c.find_previous("a", class_="hnuser")
                author = author_link.get_text() if author_link else f"commenter {i+1}"
                body_parts.append(f"### {author}\n\n{text}\n")

    return HardFetchedContent(
        url=url,
        title=title,
        body="\n\n".join(body_parts),
        meta={"site_name": "Hacker News", "item_id": item_id},
    )


# ── YouTube transcript fetcher ────────────────────────────────────

# YouTube URL patterns
YOUTUBE_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/live/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})"),
    re.compile(r"^([a-zA-Z0-9_-]{11})$"),
]

# youtube-transcript-api is optional
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    HAS_YOUTUBE_API = True
except ImportError:
    HAS_YOUTUBE_API = False


def _extract_youtube_id(url: str) -> str | None:
    """Extract 11-char YouTube video ID from any URL format."""
    url = url.strip()
    for pattern in YOUTUBE_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def _is_youtube_url(url: str) -> bool:
    """Check if a URL points to YouTube."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        return True
    # Also check for raw video IDs (11 chars)
    if _extract_youtube_id(url):
        return True
    return False


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS or MM:SS format."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


async def fetch_youtube_transcript(
    url: str,
    language: str | None = None,
    timeout: int = 30,
) -> HardFetchedContent | None:
    """Fetch a YouTube video transcript.

    Extracts the full transcript text with optional timestamps.
    Uses youtube-transcript-api (no API key required).

    Args:
        url: YouTube URL or video ID.
        language: Comma-separated language preference (e.g. 'en,zh').
        timeout: Request timeout in seconds.

    Returns:
        HardFetchedContent with video metadata and transcript,
        or None if transcript is unavailable.
    """
    if not HAS_YOUTUBE_API:
        return None

    video_id = _extract_youtube_id(url)
    if not video_id:
        return None

    try:
        # Fetch video metadata from oEmbed API
        async with httpx.AsyncClient(timeout=timeout) as client:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            oembed_resp = await client.get(oembed_url)
            video_title = ""
            author_name = ""
            if oembed_resp.status_code == 200:
                oembed_data = oembed_resp.json()
                video_title = oembed_data.get("title", "")
                author_name = oembed_data.get("author_name", "")

        # Fetch transcript using youtube-transcript-api
        yt_api: YouTubeTranscriptApi = YouTubeTranscriptApi()  # type: ignore[name-defined]
        languages = [l.strip() for l in language.split(",")] if language else None

        import asyncio as _asyncio
        def _sync_fetch():
            return yt_api.fetch(video_id, languages=languages) if languages else yt_api.fetch(video_id)

        segments = await _asyncio.to_thread(_sync_fetch)

        if not segments:
            return None

        # Normalize segments to dicts (v1.x returns objects)
        normalized = []
        for seg in segments:
            if hasattr(seg, 'text'):
                normalized.append({"text": seg.text, "start": seg.start, "duration": seg.duration})
            else:
                normalized.append(seg)

        # Build full text and timestamped version
        full_text = " ".join(seg["text"] for seg in normalized)
        timestamped_lines = []
        for seg in normalized:
            ts = _format_timestamp(seg["start"])
            timestamped_lines.append(f"{ts} {seg['text']}")

        # Get total duration
        if normalized:
            last = normalized[-1]
            total_duration = _format_timestamp(last["start"] + last["duration"])
        else:
            total_duration = "unknown"

        body_parts = [
            f"**影片長度：** {total_duration}",
            f"**片段數：** {len(normalized)}",
            "",
            "## 逐字稿",
            "",
            "\n".join(timestamped_lines),
        ]

        return HardFetchedContent(
            url=url,
            title=video_title or f"YouTube: {video_id}",
            body="\n".join(body_parts),
            meta={
                "author": author_name,
                "site_name": "YouTube",
                "video_id": video_id,
                "duration": total_duration,
                "segment_count": len(normalized),
            },
        )

    except Exception:
        return None


# ── Generic fetcher (delegates to article_fetcher) ─────────────────

async def fetch_generic(url: str, timeout: int = 20, debug: bool = False) -> HardFetchedContent | None:
    """Fetch any URL using the standard article_fetcher pipeline.

    Tries curl_cffi first, then httpx, then Playwright for JS-rendered pages.
    """
    from crawler.article_fetcher import fetch_article as _fetch_article

    result = await _fetch_article(url, timeout=timeout, debug=debug)
    if result is None:
        return None

    return HardFetchedContent(
        url=result.url,
        title=result.title,
        body=result.body,
        meta={
            "author": result.author,
            "date": result.date,
            "site_name": result.site_name,
        },
    )


# ── Smart router ───────────────────────────────────────────────────

def classify_url(url: str) -> str:
    """Classify a URL to choose the best fetcher.

    Returns: "arxiv" | "hackernews" | "youtube" | "generic"
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if "arxiv.org" in host:
        return "arxiv"
    if "ycombinator.com" in host or "hackernews" in host:
        return "hackernews"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    # Also check raw video IDs
    if _extract_youtube_id(url) and not host:
        return "youtube"
    return "generic"


async def fetch_any(url: str, timeout: int = 20, debug: bool = False) -> HardFetchedContent | None:
    """Smart fetch: auto-detect site type and use best fetcher.

    Usage:
        content = await fetch_any("https://arxiv.org/abs/2606.06635")
        print(content.to_markdown())
    """
    site_type = classify_url(url)

    if site_type == "arxiv":
        return await fetch_arxiv_abstract(url, timeout)
    elif site_type == "hackernews":
        return await fetch_hackernews_thread(url, timeout=timeout)
    elif site_type == "youtube":
        return await fetch_youtube_transcript(url)
    else:
        return await fetch_generic(url, timeout, debug=debug)


async def fetch_batch(urls: list[str], timeout: int = 20) -> list[HardFetchedContent | None]:
    """Fetch multiple URLs in parallel.

    Returns a list with results in the same order as input URLs.
    None entries indicate fetch failures.
    """
    tasks = [fetch_any(url, timeout) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Replace exceptions with None
    return [
        None if isinstance(r, (Exception, BaseException)) else r
        for r in results
    ]

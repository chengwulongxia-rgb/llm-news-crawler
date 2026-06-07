"""Hacker News API crawler.

Uses the official Firebase API — no scraping needed.
Docs: https://github.com/HackerNews/API
"""

import asyncio
from datetime import datetime, timezone

import httpx

from crawler.models import NewsItem

HN_BASE = "https://hacker-news.firebaseio.com/v0"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"

# 一次抓取的故事數量
TOP_STORIES_LIMIT = 50
NEW_STORIES_LIMIT = 100


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict | list | None:  # type: ignore[return]
    """Fetch JSON from HN API with retry."""
    for attempt in range(3):
        try:
            resp = await client.get(url, timeout=15.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
    return None


async def fetch_story_ids(client: httpx.AsyncClient, endpoint: str, limit: int) -> list[int]:
    """Fetch story IDs from topstories/newstories/beststories."""
    url = f"{HN_BASE}/{endpoint}.json"
    ids = await _fetch_json(client, url)
    if isinstance(ids, list):
        return ids[:limit]
    return []


async def fetch_item(client: httpx.AsyncClient, item_id: int) -> dict | None:
    """Fetch a single item (story/comment) by ID."""
    url = f"{HN_BASE}/item/{item_id}.json"
    return await _fetch_json(client, url)


def _item_to_news(item: dict) -> NewsItem:
    """Convert HN API item dict to NewsItem."""
    ts = item.get("time", 0)
    published_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

    return NewsItem(
        title=item.get("title", "(no title)"),
        url=item.get("url") or HN_ITEM_URL.format(id=item.get("id")),
        source="HackerNews",
        score=item.get("score", 0),
        comments=item.get("descendants", 0),
        author=item.get("by", ""),
        published_at=published_at,
    )


async def fetch_hackernews(
    client: httpx.AsyncClient | None = None,
    top_limit: int = TOP_STORIES_LIMIT,
    new_limit: int = NEW_STORIES_LIMIT,
    concurrency: int = 20,
) -> list[NewsItem]:
    """Fetch top + new stories from HN, return as NewsItem list.

    Args:
        client: Optional shared httpx.AsyncClient.
        top_limit: Max top stories to fetch.
        new_limit: Max new stories to fetch.
        concurrency: Max concurrent item fetches.

    Returns:
        List of NewsItem objects (unfiltered).
    """
    close_client = False
    if client is None:
        client = httpx.AsyncClient()
        close_client = True

    try:
        # Fetch story IDs
        top_ids, new_ids = await asyncio.gather(
            fetch_story_ids(client, "topstories", top_limit),
            fetch_story_ids(client, "newstories", new_limit),
        )

        # Deduplicate
        all_ids = list(dict.fromkeys(top_ids + new_ids))

        # Fetch items concurrently
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_with_limit(item_id: int) -> NewsItem | None:
            async with semaphore:
                item = await fetch_item(client, item_id)
                if item and item.get("type") == "story" and item.get("title"):
                    return _item_to_news(item)
                return None

        tasks = [fetch_with_limit(iid) for iid in all_ids]
        results = await asyncio.gather(*tasks)

        return [r for r in results if r is not None]

    finally:
        if close_client:
            await client.aclose()

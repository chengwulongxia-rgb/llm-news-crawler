"""Cross-run deduplication for crawler URLs.

Tracks seen URLs in a JSON file, expires entries older than N hours.
This prevents the same hot story from appearing in every collection run.
"""

import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger("crawler.dedup")

DEFAULT_DEDUP_FILE = Path.home() / ".hermes" / "crawler_seen_urls.json"
DEFAULT_TTL_HOURS = 48  # URLs expire after 48 hours


class DedupStore:
    """Persistent URL deduplication store."""

    def __init__(self, path: Path = DEFAULT_DEDUP_FILE, ttl_hours: int = DEFAULT_TTL_HOURS):
        self.path = path
        self.ttl_seconds = ttl_hours * 3600
        self._data: dict[str, float] = {}  # url -> timestamp
        self._load()

    def _load(self):
        """Load seen URLs from disk."""
        if not self.path.exists():
            logger.info("去重檔案不存在：%s（首次執行？）", self.path)
            return
        try:
            with open(self.path) as f:
                self._data = json.load(f)
            logger.info("去重：從 %s 載入 %d 筆 URL", self.path, len(self._data))
        except json.JSONDecodeError as e:
            logger.error("去重檔案 JSON 格式錯誤：%s — 略過，從空開始（錯誤：%s）", self.path, e)
            self._data = {}
        except IOError as e:
            logger.error("去重檔案讀取失敗：%s — 略過，從空開始（錯誤：%s）", self.path, e)
            self._data = {}

    def _save(self):
        """Save seen URLs to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, "w") as f:
                json.dump(self._data, f, indent=2)
        except IOError as e:
            logger.error("去重檔案寫入失敗：%s（錯誤：%s）", self.path, e)

    def _expire(self):
        """Remove entries older than TTL."""
        now = time.time()
        cutoff = now - self.ttl_seconds
        expired = [url for url, ts in self._data.items() if ts < cutoff]
        for url in expired:
            del self._data[url]
        if expired:
            logger.info("去重：過期 %d 筆（>%dh TTL），剩餘 %d 筆", len(expired), self.ttl_seconds // 3600, len(self._data))
            self._save()
        return len(expired)

    def is_seen(self, url: str) -> bool:
        """Check if URL has been seen within TTL window."""
        self._expire()
        return url in self._data

    def mark_seen(self, url: str):
        """Mark a URL as seen."""
        self._data[url] = time.time()

    def mark_seen_batch(self, urls: list[str]):
        """Mark multiple URLs as seen."""
        now = time.time()
        for url in urls:
            self._data[url] = now
        self._save()
        logger.info("去重：標記 %d 筆新 URL（總計 %d 筆）", len(urls), len(self._data))

    def filter_new(self, urls: list[str]) -> tuple[list[str], list[str]]:
        """Split URLs into (new, already_seen).

        Returns:
            (new_urls, seen_urls) — both are subsets of input.
        """
        self._expire()
        new = []
        seen = []
        for url in urls:
            if url in self._data:
                seen.append(url)
            else:
                new.append(url)
        return new, seen

    def clear(self):
        """Clear all tracked URLs."""
        self._data = {}
        self._save()

    def stats(self) -> dict:
        """Return stats about the store."""
        self._expire()
        return {
            "total_tracked": len(self._data),
            "ttl_hours": self.ttl_seconds / 3600,
            "file": str(self.path),
        }

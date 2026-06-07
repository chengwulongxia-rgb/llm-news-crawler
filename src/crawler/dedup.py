"""Cross-run deduplication for crawler URLs.

Tracks seen URLs in a JSON file, expires entries older than N hours.
This prevents the same hot story from appearing in every collection run.
"""

import json
import time
from pathlib import Path

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
        if self.path.exists():
            try:
                with open(self.path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}

    def _save(self):
        """Save seen URLs to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def _expire(self):
        """Remove entries older than TTL."""
        now = time.time()
        cutoff = now - self.ttl_seconds
        expired = [url for url, ts in self._data.items() if ts < cutoff]
        for url in expired:
            del self._data[url]
        if expired:
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

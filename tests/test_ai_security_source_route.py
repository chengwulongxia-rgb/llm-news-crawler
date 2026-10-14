"""Integration tests for the AI Security Watch source route."""

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from crawler.main import run
from crawler.models import NewsItem


class AiSecuritySourceRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_security_source_is_collected(self):
        incident = NewsItem(
            title="AI agent used in personal data breach",
            url="https://example.test/incident",
            source="AI Security Watch",
            score=50,
            published_at=datetime.now(UTC),
        )
        with patch("crawler.main.fetch_ai_security_news", new=AsyncMock(return_value=[incident])):
            items = await run(["ai-security"], min_score=10)

        self.assertEqual([item.url for item in items], ["https://example.test/incident"])


    async def test_ai_powered_incident_survives_general_title_filter(self):
        incident = NewsItem(
            title="AI-powered cyberattack breached hospital network",
            url="https://example.test/hospital",
            source="AI Security Watch",
            score=50,
            published_at=datetime.now(UTC),
        )
        with patch("crawler.main.fetch_ai_security_news", new=AsyncMock(return_value=[incident])):
            items = await run(["ai-security"], min_score=10, max_age_days=3)
        self.assertEqual([item.url for item in items], ["https://example.test/hospital"])


if __name__ == "__main__":
    unittest.main()

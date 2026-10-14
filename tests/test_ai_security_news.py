"""Tests for the targeted AI-security news source."""

import unittest
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from crawler.sources.ai_security_news import (
    fetch_ai_security_news,
    parse_google_news_rss,
    resolve_google_news_url,
)

RSS = b"""<?xml version='1.0' encoding='UTF-8'?>
<rss><channel>
  <item>
    <title>Spain reports first personal-data breach carried out by AI agent - Reuters</title>
    <link>https://news.google.com/rss/articles/abc</link>
    <pubDate>Mon, 15 Sep 2026 12:00:00 GMT</pubDate>
    <description>Spain's AEPD is investigating an AI agent attack.</description>
  </item>
  <item>
    <title>Unrelated sports result - Example</title>
    <link>https://news.google.com/rss/articles/def</link>
    <pubDate>Mon, 15 Sep 2026 13:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


class ParseGoogleNewsRssTests(unittest.TestCase):
    def test_keeps_ai_security_incident_and_normalizes_publication_time(self):
        items = parse_google_news_rss(RSS, source_name="AI Security Watch")

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "Spain reports first personal-data breach carried out by AI agent")
        self.assertEqual(item.source, "AI Security Watch")
        self.assertEqual(item.score, 50)
        self.assertEqual(item.url, "https://news.google.com/rss/articles/abc")
        self.assertIsNotNone(item.published_at)
        assert item.published_at is not None
        self.assertEqual(item.published_at.tzinfo, UTC)
        self.assertIn("AEPD", item.summary)

    def test_excludes_how_to_guides_that_are_not_incidents(self):
        guide = b"""<rss><channel><item>
          <title>How to Structure an AI Agent Data Breach Notification Clause Before You Sign - Example</title>
          <link>https://news.google.com/rss/articles/guide</link>
          <pubDate>Wed, 14 Oct 2026 12:00:00 GMT</pubDate>
        </item></channel></rss>"""
        self.assertEqual(parse_google_news_rss(guide, "AI Security Watch"), [])

    def test_excludes_generic_vulnerability_guides(self):
        guide = b"""<rss><channel><item>
          <title>AI agent vulnerability guide - Example</title>
          <link>https://news.google.com/rss/articles/guide</link>
          <pubDate>Wed, 14 Oct 2026 12:00:00 GMT</pubDate>
        </item></channel></rss>"""
        self.assertEqual(parse_google_news_rss(guide, "AI Security Watch"), [])

    def test_skips_items_without_valid_publication_date(self):
        undated = b"""<rss><channel><item>
          <title>AI agent hacked a database - Reporter</title>
          <link>https://news.google.com/rss/articles/old</link>
          <pubDate>not a date</pubDate>
        </item></channel></rss>"""
        self.assertEqual(parse_google_news_rss(undated, "AI Security Watch"), [])

    def test_resolves_google_news_redirect_for_article_fetching(self):
        with (
            patch(
                "crawler.sources.ai_security_news.gnewsdecoder",
                return_value={"status": True, "decoded_url": "https://example.test/article"},
            ),
            patch("crawler.sources.ai_security_news.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.215.14", 443))]),
        ):
            resolved = resolve_google_news_url("https://news.google.com/rss/articles/abc")

        self.assertEqual(resolved, "https://example.test/article")

    def test_rejects_private_decoded_destinations(self):
        with patch(
            "crawler.sources.ai_security_news.gnewsdecoder",
            return_value={"status": True, "decoded_url": "http://127.0.0.1/private"},
        ):
            self.assertIsNone(resolve_google_news_url("https://news.google.com/rss/articles/abc"))

    def test_rejects_hostname_that_resolves_to_private_address(self):
        with (
            patch(
                "crawler.sources.ai_security_news.gnewsdecoder",
                return_value={"status": True, "decoded_url": "https://attacker.example/private"},
            ),
            patch("crawler.sources.ai_security_news.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]),
        ):
            self.assertIsNone(resolve_google_news_url("https://news.google.com/rss/articles/abc"))

    def test_keeps_spanish_ai_security_title(self):
        spanish = b"""<rss><channel><item>
          <title>Primera brecha de datos personales causada por un agente de IA - AEPD</title>
          <link>https://news.google.com/rss/articles/ghi</link>
          <pubDate>Mon, 15 Sep 2026 12:00:00 GMT</pubDate>
        </item></channel></rss>"""

        items = parse_google_news_rss(spanish, source_name="AI Security Watch")

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0].title,
            "Primera brecha de datos personales causada por un agente de IA",
        )


class FeedFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_failed_feeds_are_not_reported_as_no_news(self):
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("network down")
        with self.assertRaisesRegex(RuntimeError, "all.*failed"):
            await fetch_ai_security_news(client)


    async def test_all_decode_failures_are_not_reported_as_no_news(self):
        client = AsyncMock()
        client.get.return_value = MagicMock(content=RSS)
        with (
            patch("crawler.sources.ai_security_news.resolve_google_news_url", return_value=None),
            self.assertRaisesRegex(RuntimeError, "decod"),
        ):
            await fetch_ai_security_news(client)


if __name__ == "__main__":
    unittest.main()

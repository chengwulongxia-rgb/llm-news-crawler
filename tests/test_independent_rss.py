"""Regression tests for independent writer feeds and their output."""

import json
import sys

from crawler.sources.rss_feeds import RSS_SOURCES, parse_rss_feed
from crawler.main import format_source_counts
from crawler.models import NewsItem


def test_independent_feeds_are_configured():
    expected = {
        "Simon Willison": "https://simonwillison.net/atom/entries/",
        "Interconnects": "https://www.interconnects.ai/feed",
        "Latent Space": "https://www.latent.space/feed",
        "Sebastian Raschka": "https://magazine.sebastianraschka.com/feed",
    }
    found = {source["name"]: source["url"] for source in RSS_SOURCES}
    assert {name: found.get(name) for name in expected} == expected


def test_configured_score_is_used_for_independent_rss():
    config = {
        "name": "Independent author",
        "item_tag": "item",
        "title_tag": "title",
        "link_tag": "link",
        "desc_tag": "description",
        "date_tag": "pubDate",
        "date_format": "rfc2822",
        "strip_cdata": False,
        "score": 35,
    }
    xml = "<rss><channel><item><title>LLM field notes</title><link>https://example.org/notes</link><pubDate>Tue, 13 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>"
    item = parse_rss_feed(xml, config)[0]
    assert item.score == 35
    assert item.published_at is not None


def test_atom_feed_picks_article_link_not_comments():
    config = next(s for s in RSS_SOURCES if s["name"] == "Simon Willison")
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <title>Claude field notes</title>
        <link rel="replies" href="https://example.org/comments" />
        <link rel="alternate" type="text/html" href="https://example.org/article" />
        <published>2026-10-13T12:00:00Z</published>
        <summary>Research notes.</summary>
    </entry></feed>'''
    item = parse_rss_feed(xml, config)[0]
    assert item.url == "https://example.org/article"
    assert item.published_at is not None
    assert item.summary == "Research notes."


def test_source_counts_report_selected_items_only():
    selected = [
        NewsItem(title="a", url="https://a.example", source="Simon Willison", score=35),
        NewsItem(title="b", url="https://b.example", source="HackerNews", score=10),
        NewsItem(title="c", url="https://c.example", source="Simon Willison", score=35),
    ]
    assert format_source_counts(selected) == "📊 入選來源（3 則）：HackerNews 1、Simon Willison 2"
    assert format_source_counts([]) == "📊 入選來源（0 則）：無"


def test_text_cli_includes_source_counts_in_stdout(monkeypatch, capsys):
    import crawler.main as crawler_main

    async def run_stub(*args, **kwargs):
        return [NewsItem(title="Claude field notes", url="https://example.org/ai", source="Simon Willison", score=35)]

    monkeypatch.setattr(crawler_main, "run", run_stub)
    monkeypatch.setattr(sys, "argv", ["llm-crawler", "--no-dedup"])
    crawler_main.main()
    captured = capsys.readouterr()
    assert "• Claude field notes" in captured.out
    assert "📊 入選來源（1 則）：Simon Willison 1" in captured.out


def test_json_cli_keeps_machine_readable_output(monkeypatch, capsys):
    import crawler.main as crawler_main

    async def run_stub(*args, **kwargs):
        return [NewsItem(title="Claude field notes", url="https://example.org/ai", source="Simon Willison", score=35)]

    monkeypatch.setattr(crawler_main, "run", run_stub)
    monkeypatch.setattr(sys, "argv", ["llm-crawler", "--json", "--no-dedup"])
    crawler_main.main()
    assert json.loads(capsys.readouterr().out)[0]["source"] == "Simon Willison"

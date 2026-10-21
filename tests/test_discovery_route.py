"""Discovery is not truncated, heat-gated, or consumed by the daily news stream."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from crawler.models import NewsItem


@pytest.mark.parametrize(
    "extra",
    [
        ["--deep-dive-budget", "0"],
        ["--deep-dive-budget", "41"],
        ["--deep-dive-output", "bad.md"],
        ["--output", "latest.json"],
        ["--output", "latest.md"],
        ["--fetch", "https://example.org/post"],
    ],
)
def test_invalid_discovery_options_fail_before_collection(extra, monkeypatch):
    import crawler.main as cli

    monkeypatch.setattr(
        cli,
        "run",
        AsyncMock(side_effect=AssertionError("invalid options reached collection")),
    )
    monkeypatch.setattr(
        cli,
        "fetch_article",
        AsyncMock(side_effect=AssertionError("invalid options reached fetch")),
    )
    monkeypatch.setattr(
        sys, "argv", ["llm-crawler", "--deep-dive-output", "latest.json", *extra]
    )
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 2


def test_cli_writes_separate_cards_without_polluting_news_json(
    tmp_path, monkeypatch, capsys
):
    import crawler.main as cli
    from crawler import discovery
    from crawler.evidence_fetch import Document

    news = NewsItem(
        title="GPT launch", url="https://openai.com/test", source="OpenAI", score=1000
    )
    lead = NewsItem(
        title="An experiment",
        url="https://simonwillison.net/test",
        source="Simon Willison",
        score=0,
    )

    async def collect(*args, **kwargs):
        kwargs["discovery_pool"].append(lead)
        return [news, lead]

    monkeypatch.setattr(cli, "run", collect)
    monkeypatch.setattr(
        discovery,
        "fetch_document",
        AsyncMock(return_value=Document("timeout", error="20s", url=lead.url)),
    )
    path = tmp_path / "latest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-crawler",
            "--no-dedup",
            "--json",
            "--limit",
            "1",
            "--deep-dive-output",
            str(path),
            "--deep-dive-budget",
            "1",
        ],
    )
    cli.main()
    captured = capsys.readouterr()
    assert [i["title"] for i in json.loads(captured.out)] == [news.title]
    report = json.loads(path.read_text())
    assert report["candidates"][0]["item"]["title"] == lead.title
    assert report["stats"]["failed"] == 1
    assert "深挖" in captured.err


def test_discovery_recovers_low_heat_and_title_misses_without_changing_news(
    monkeypatch,
):
    import crawler.main as cli

    now = datetime.now(UTC)
    raw = [
        NewsItem(
            title="A tiny experiment",
            url="https://simonwillison.net/new",
            source="Simon Willison",
            score=0,
            published_at=now,
        ),
        NewsItem(
            title="Claude launch",
            url="https://vendor.example/new",
            source="HackerNews",
            score=1000,
            published_at=now,
        ),
        NewsItem(
            title="An old LLM experiment",
            url="https://example.org/old",
            source="Simon Willison",
            score=1,
            published_at=now - timedelta(days=90),
        ),
    ]
    monkeypatch.setattr(cli, "fetch_all_rss", AsyncMock(return_value=raw))
    pool = []
    news = asyncio.run(
        cli.run(["rss"], min_score=10, max_age_days=3, discovery_pool=pool)
    )
    assert [i.url for i in news] == [raw[1].url]
    assert {i.url for i in pool} == {raw[0].url, raw[1].url}


def test_hn_retains_discussion_provenance_even_for_external_articles():
    from crawler.sources.hackernews import _item_to_news

    item = _item_to_news(
        {
            "id": 123,
            "title": "LLM demo",
            "url": "https://example.org/demo",
            "score": 3,
            "descendants": 7,
        }
    )
    assert item.url == "https://example.org/demo"
    assert item.discussion_url == "https://news.ycombinator.com/item?id=123"
    assert item.comments == 7

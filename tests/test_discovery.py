"""A separately budgeted evidence pass must not consume daily dedup or stdout."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from crawler.models import NewsItem


def test_budget_favors_concrete_technical_leads_over_newer_talks():
    from crawler.discovery import select_seeds

    items = [
        NewsItem(
            title="Anthropic talk",
            url="https://example.org/talk",
            source="HackerNews",
            score=9999,
            published_at=datetime.now(UTC),
        ),
        NewsItem(
            title="DeepInfer: open-source inference framework benchmark",
            url="https://github.com/example/DeepInfer",
            source="HackerNews",
            score=1,
        ),
    ]
    selected, eligible = select_seeds(items, 1)
    assert eligible == 2 and selected[0].url == items[1].url


def test_report_budget_ignores_heat_preserves_failures_and_archives_excerpt_sources(
    tmp_path, monkeypatch
):
    from crawler import discovery
    from crawler.evidence_fetch import Document

    now = datetime.now(UTC)
    items = [
        NewsItem(
            title="An experiment",
            url="https://simonwillison.net/test",
            source="Simon Willison",
            score=0,
            published_at=now,
        ),
        NewsItem(
            title="GPT announcement",
            url="https://openai.com/test",
            source="OpenAI",
            score=9000,
            published_at=now,
        ),
        NewsItem(
            title="Unrelated bicycle",
            url="https://other.example/bike",
            source="HackerNews",
            score=900000,
            published_at=now,
        ),
    ]
    body = "Interpreter stack.\nReproduce with uv run main.py.\nExpected output is 7.\nhttps://github.com/example/tool"
    called = []

    async def fetch(url, client):
        called.append(url)
        if "openai" in url:
            return Document("timeout", error="20s", url=url)
        return Document(
            "ok", body=body, url=url, sha256=hashlib.sha256(body.encode()).hexdigest()
        )

    monkeypatch.setattr(discovery, "fetch_document", fetch)
    path = tmp_path / "latest.json"
    report = asyncio.run(discovery.create_report(items + [items[0]], path, budget=2))
    assert len(called) == len(set(called)) == 2
    assert report["stats"] == {
        "pool": 4,
        "eligible": 2,
        "fetched": 2,
        "ok": 1,
        "failed": 1,
        "proposals": 1,
        "not_fetched": 0,
    }
    assert report["candidates"][0]["item"]["title"] == "An experiment"
    assert report["candidates"][1]["fetch_status"] == "timeout"
    assert json.loads(path.read_text()) == report
    digest = hashlib.sha256(body.encode()).hexdigest()
    assert (tmp_path / "snapshots" / f"{digest}.txt").read_text() == body
    assert "待驗證" in path.with_suffix(".md").read_text()
    assert all(c["experiment_status"] == "not_run" for c in report["candidates"])

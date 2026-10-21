"""Offline regressions for bounded evidence and isolated candidate failures."""

import asyncio
import gzip
import json
import sys
from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest


@pytest.mark.parametrize("encoding", ["gzip", "GZip", "br", "identity, gzip"])
def test_encoded_evidence_is_rejected_before_reading(encoding, monkeypatch):
    from crawler import evidence_fetch

    monkeypatch.setattr(evidence_fetch, "_is_public_article_url", lambda url: True)
    reads = []
    requests = []

    class CompressedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            reads.append(True)
            yield gzip.compress(b"x" * 100_000)

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain", "Content-Encoding": encoding},
            stream=CompressedStream(),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await evidence_fetch.fetch_document(
                "https://public.example/article", client, max_bytes=100
            )

    result = asyncio.run(scenario())
    assert result.status == "unsupported_encoding"
    assert not result.body
    assert not reads
    assert requests[0].headers["accept-encoding"] == "identity"


def test_invalid_material_token_is_retained_as_gap_without_repair():
    from crawler.editorial import assess
    from crawler.models import NewsItem

    token = "https://github.com／evil/repo"
    item = NewsItem(title="LLM parser", url="https://example.org/post", source="RSS")
    card = assess(item, f"parser mechanism\n{token}")
    assert not card.materials
    assert any(token in gap and "無效" in gap for gap in card.gaps)
    assert "mechanism" in {clue.kind for clue in card.evidence}
    mixed = assess(item, f"{token}\nhttps://github.com/valid/project")
    assert mixed.materials == ["https://github.com/valid/project"]
    assert any(token in gap for gap in mixed.gaps)


@pytest.mark.parametrize("stage", ["assessment", "snapshot", "json", "markdown"])
@pytest.mark.parametrize("output_mode", ["text", "json", "json_file"])
def test_candidate_failure_preserves_news(
    stage, output_mode, tmp_path, monkeypatch, capsys
):
    import crawler.main as cli
    from crawler import discovery
    from crawler.dedup import DedupStore
    from crawler.evidence_fetch import Document
    from crawler.models import NewsItem

    news = NewsItem(
        title="LLM parser experiment", url="https://example.org/post", source="RSS",
        score=100,
    )
    store = DedupStore(tmp_path / "seen.json")
    monkeypatch.setattr(cli, "DedupStore", lambda: store)
    monkeypatch.setattr(cli, "fetch_all_rss", AsyncMock(return_value=[news]))
    monkeypatch.setattr(
        discovery, "fetch_document",
        AsyncMock(return_value=Document("ok", body="parser mechanism " * 20, sha256="abc")),
    )
    if stage == "assessment":
        original_assess = discovery.assess

        def fail_assessment(item, body, **kwargs):
            if body is not None:
                raise ValueError("assessment failed")
            return original_assess(item, body, **kwargs)

        monkeypatch.setattr(discovery, "assess", fail_assessment)
    else:
        original_replace = discovery.os.replace

        def fail_write(source, target):
            is_target = (
                (stage == "snapshot" and target.suffix == ".txt")
                or (stage == "json" and target.suffix == ".json")
                or (stage == "markdown" and target.suffix == ".md")
            )
            if is_target:
                raise OSError(f"{stage} disk full")
            original_replace(source, target)

        monkeypatch.setattr(discovery.os, "replace", fail_write)

    report_path = tmp_path / "candidate.json"
    argv = ["llm-crawler", "--sources", "rss", "--deep-dive-output", str(report_path)]
    if output_mode != "text":
        argv.append("--json")
    news_path = tmp_path / "news.json"
    if output_mode == "json_file":
        argv.extend(["--output", str(news_path)])
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() is None  # A successful news stream retains exit 0.
    captured = capsys.readouterr()
    if output_mode == "text":
        expected = cli.format_collector_output([news], datetime.now().strftime("%Y-%m-%d"))
        assert captured.out == expected + "\n" + cli.format_source_counts([news]) + "\n"
    else:
        output = news_path.read_text() if output_mode == "json_file" else captured.out
        assert json.loads(output) == [news.model_dump(mode="json")]
        if output_mode == "json_file":
            assert not captured.out
    assert store.is_seen(news.url)
    assert "深挖候選失敗" in captured.err
    assert str(report_path) in captured.err
    assert ("ValueError" if stage == "assessment" else "OSError") in captured.err
    assert ("assessment failed" if stage == "assessment" else f"{stage} disk full") in captured.err
    assert "正文 1/1" not in captured.err

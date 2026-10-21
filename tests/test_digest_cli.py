"""The digest path keeps all layout in code and blocks malformed model output."""

import json
import subprocess
from pathlib import Path

import pytest


def test_worker_failure_keeps_diagnostic_and_produces_no_report(monkeypatch, capsys):
    import sys

    import crawler.digest_cli as cli

    monkeypatch.setattr(
        cli,
        "worker_command",
        lambda *args: [
            sys.executable,
            "-c",
            'import sys; print("provider unavailable", file=sys.stderr); sys.exit(1)',
        ],
    )
    with pytest.raises(subprocess.CalledProcessError):
        cli.summarize_manifest(
            {
                "entries": [
                    {"id": "1", "title": "News", "summary": "Text", "candidate": None}
                ]
            },
            "job",
        )
    captured = capsys.readouterr()
    assert captured.out == "" and "provider unavailable" in captured.err


def test_recent_successful_analysis_supersedes_older_keyword_noise(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from crawler.digest import build_manifest

    url = "https://example.org/ai"
    item = {"title": "AI notes", "url": url, "source": "HackerNews"}
    older = {
        "item": item,
        "fetch_status": "ok",
        "priority": 12,
        "evidence": [
            {"kind": "mechanism", "excerpt": "pay attention", "url": url},
            {"kind": "comparison", "excerpt": "comparison", "url": url},
        ],
    }
    newer = {
        "item": item,
        "fetch_status": "ok",
        "priority": 4,
        "evidence": [{"kind": "comparison", "excerpt": "comparison", "url": url}],
    }
    for name, stamp, card in [
        ("old", "2026-10-21T01:00:00Z", older),
        ("new", "2026-10-21T02:00:00Z", newer),
    ]:
        (tmp_path / f"2026-10-21_{name}.json").write_text(
            json.dumps({"generated_at": stamp, "candidates": [card]})
        )
    manifest = build_manifest(
        tmp_path / "none",
        tmp_path,
        datetime(2026, 10, 21, 16, 7, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    assert manifest["entries"][0]["candidate"]["priority"] == 4


def test_regular_news_is_compact_and_ends_with_selection_request():
    from crawler.digest import render_digest

    manifest = {
        "generated_at": "2026-10-21T16:07:00+08:00",
        "days": 2,
        "entries": [
            {
                "id": "1",
                "title": "News",
                "url": "https://example.org/a",
                "candidate": None,
            }
        ],
        "warnings": [],
        "excluded": [],
    }
    text = render_digest(manifest, {"1": "來源公告一項工具。"})
    assert "分類：未分類" not in text and "來源線索／缺口" not in text
    assert "給我編號" in text


def test_legacy_english_month_and_date_slug_articles_are_not_presented_as_recent(
    tmp_path,
):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from crawler.digest import build_manifest

    collector = tmp_path / "collector"
    collector.mkdir()
    (collector / "2026-10-21_am.md").write_text(
        "• Old Simon — Past post — 來源: https://simonwillison.net/2026/Sep/29/old/\n• Old Reuters — Past report — 來源: https://www.reuters.com/news/something-2026-10-19/\n• Current — Recent post — 來源: https://simonwillison.net/2026/Oct/21/current/\n"
    )
    manifest = build_manifest(
        collector,
        tmp_path / "candidates",
        datetime(2026, 10, 21, 16, 7, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    assert [e["title"] for e in manifest["entries"]] == ["Current"]
    assert len(manifest["excluded"]) == 2 and manifest["warnings"]


def test_checked_pipeline_writes_exact_report_only_after_validation(
    tmp_path, monkeypatch
):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import crawler.digest_cli as cli

    collector = tmp_path / "collector"
    collector.mkdir()
    (collector / "2026-10-21_am.md").write_text(
        "• First title — First summary — 來源: https://github.com/deepseek-ai/DeepInfer\n• Other title — Other summary — 來源: https://github.com/deepseek-ai/DeepInfer\n"
    )
    monkeypatch.setattr(
        cli,
        "summarize_manifest",
        lambda manifest, job_id: {
            "summaries": {"1": "來源介紹一套推論框架。"},
            "model": "existing",
            "provider": "existing",
        },
    )
    now = datetime(2026, 10, 21, 16, 7, tzinfo=ZoneInfo("Asia/Taipei"))
    text, output = cli.run_digest(
        collector, tmp_path / "candidates", tmp_path / "out", "job", now=now
    )
    assert "16:07:00" in text and "共 1 則" in text
    assert text.count("https://github.com/deepseek-ai/DeepInfer") == 1
    assert (output / "report.md").read_text() == text
    assert json.loads((output / "validation.json").read_text())["validated"] is True


def test_invalid_summary_never_writes_a_deliverable_report(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import crawler.digest_cli as cli

    collector = tmp_path / "collector"
    collector.mkdir()
    (collector / "2026-10-21_am.md").write_text(
        "• Title — Summary — 來源: https://example.org/article\n"
    )
    monkeypatch.setattr(
        cli,
        "summarize_manifest",
        lambda *args: {
            "summaries": {"1": "fake https://wrong.example"},
            "model": "existing",
            "provider": "existing",
        },
    )
    with pytest.raises(ValueError):
        cli.run_digest(
            collector,
            tmp_path / "candidates",
            tmp_path / "out",
            "job",
            now=datetime(2026, 10, 21, 16, 7, tzinfo=ZoneInfo("Asia/Taipei")),
        )
    assert not list((tmp_path / "out").glob("**/report.md"))


def test_noon_wrapper_runs_checked_renderer_not_freeform_agent(tmp_path):
    import os

    home = tmp_path / "home"
    project = home / "projects/llm-news-crawler"
    project.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "args.json"
    uv = bin_dir / "uv"
    uv.write_text(
        '#!/usr/bin/env python3\nimport json,os,sys\nopen(os.environ["CALL_LOG"],"w").write(json.dumps(sys.argv[1:]))\n'
    )
    uv.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "scripts/noon-digest.sh"
    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(home),
            "CALL_LOG": str(log),
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(log.read_text()) == [
        "run",
        "llm-digest",
        "--job-id",
        "e0349bb0f3b1",
    ]


def test_summary_model_receives_only_bounded_article_data(monkeypatch):
    import crawler.digest_cli as cli

    monkeypatch.setattr(cli, "worker_command", lambda *args: ["summary-worker"])

    def invoke(command, **kwargs):
        payload = json.loads(kwargs["input"])
        assert command == ["summary-worker"] and kwargs["timeout"] == 130
        assert payload["entries"][0]["id"] == "1"
        assert "unrelated_secret" not in kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            0,
            '{"summaries":{"1":"來源介紹一項工具。"},"model":"existing","provider":"existing"}',
            "",
        )

    monkeypatch.setattr(subprocess, "run", invoke)
    result = cli.summarize_manifest(
        {
            "entries": [
                {
                    "id": "1",
                    "title": "A tool",
                    "summary": "A source summary",
                    "candidate": None,
                    "unrelated_secret": "do-not-forward",
                }
            ]
        },
        "job",
    )
    assert result["summaries"]["1"] == "來源介紹一項工具。"


def test_empty_manifest_needs_no_model(monkeypatch):
    import crawler.digest_cli as cli

    monkeypatch.setattr(
        cli, "worker_command", lambda *args: pytest.fail("empty manifest called model")
    )
    assert cli.summarize_manifest({"entries": []}, "job")["summaries"] == {}


def test_worker_command_uses_discovered_hermes_bootstrap_and_argv(monkeypatch):
    from crawler.digest_cli import worker_command

    bootstrap = "import sys, runpy; import hermes_bootstrap; runpy.run_module('runpy', run_name='__main__', alter_sys=True)"

    def invoke(command, **kwargs):
        assert command == ["hermes", "--print-runtime-command", "--module", "runpy"]
        return subprocess.CompletedProcess(
            command, 0, json.dumps(["/runtime/python", "-I", "-c", bootstrap]), ""
        )

    monkeypatch.setattr(subprocess, "run", invoke)
    command = worker_command(Path("/repo/scripts/digest_summarize.py"), "existing-job")
    assert command[:3] == ["/runtime/python", "-I", "-c"]
    assert "hermes_bootstrap" in command[3] and "sys.argv.pop(1)" in command[3]
    assert command[4:] == [
        "/repo/scripts/digest_summarize.py",
        "--job-id",
        "existing-job",
    ]


def test_unrecognized_launcher_protocol_fails_closed(monkeypatch):
    from crawler.digest_cli import worker_command

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, '["unexpected"]', ""
        ),
    )
    with pytest.raises(ValueError):
        worker_command(Path("/repo/worker.py"), "existing-job")

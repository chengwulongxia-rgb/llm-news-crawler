"""The summarizer has no tools and inherits the existing job's explicit model."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_worker():
    path = Path(__file__).resolve().parents[1] / "scripts" / "digest_summarize.py"
    spec = importlib.util.spec_from_file_location("digest_worker", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return worker


def test_worker_uses_pinned_job_pair_with_no_tools_or_memory(monkeypatch):
    worker = load_worker()
    seen = {}

    class Agent:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run_conversation(self, query):
            assert "UNTRUSTED" in query
            return {
                "completed": True,
                "final_response": '{"summaries":{"1":"來源介紹一項測試工具。"}}',
            }

    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=Agent))

    def resolve(**kwargs):
        assert kwargs == {"requested": "openai-codex", "target_model": "existing-model"}
        return {"provider": "openai-codex", "api_mode": "codex"}

    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.runtime_provider",
        SimpleNamespace(resolve_runtime_provider=resolve),
    )
    result = worker.summarize(
        {"entries": [{"id": "1", "title": "A test"}]},
        {"model": "existing-model", "provider": "openai-codex"},
    )
    assert result["summaries"] == {"1": "來源介紹一項測試工具。"}
    assert seen["enabled_toolsets"] == [] and seen["skip_memory"] is True
    assert seen["skip_context_files"] is True and seen["load_soul_identity"] is False
    assert seen["model"] == "existing-model"


@pytest.mark.parametrize("job", [{}, {"model": "only-model"}])
def test_missing_job_pair_fails_instead_of_selecting_a_different_model(job):
    worker = load_worker()
    with pytest.raises(ValueError):
        worker.summarize({"entries": []}, job)

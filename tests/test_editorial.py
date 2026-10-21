"""Editorial heuristics use source excerpts, not popularity or unrun claims."""

from crawler.models import NewsItem


def test_business_attention_and_production_stack_are_not_mechanism_evidence():
    from crawler.editorial import assess

    item = NewsItem(
        title="AI business interview",
        url="https://example.org/interview",
        source="HackerNews",
    )
    card = assess(
        item, "We pay attention to customer relationships and our production stack."
    )
    assert "mechanism" not in {e.kind for e in card.evidence}


def test_malformed_source_url_does_not_crash_assessment():
    from crawler.editorial import assess

    item = NewsItem(title="LLM notes", url="http://[invalid", source="HackerNews")
    card = assess(item, None, fetch_status="blocked_url")
    assert card.fetch_status == "blocked_url" and not card.evidence


def test_roundup_cannot_combine_different_stories_into_a_case():
    from crawler.editorial import assess

    item = NewsItem(
        title="[AINews] This week's AI news",
        url="https://www.latent.space/p/news",
        source="Latent Space",
    )
    body = "Story one: reproduce the demo with uv run main.py.\nStory two: expected output is 7.\nhttps://github.com/example/unrelated"
    card = assess(item, body)
    assert "roundup" in card.content_types
    assert card.implementation_potential == "none" and not card.case_idea
    assert card.gaps


def test_negated_reproducibility_and_python_version_are_not_run_instructions():
    from crawler.editorial import assess

    item = NewsItem(
        title="LLM release", url="https://example.org/new", source="HackerNews"
    )
    card = assess(
        item,
        "This release is not reproducible.\nExpected output is unknown.\nHello Python 3.15, goodbye 3.10.\nhttps://github.com/example/tool",
    )
    assert card.implementation_potential == "none"
    assert "reproduction" not in {e.kind for e in card.evidence}


def test_ranking_uses_body_evidence_not_heat_company_or_link_count():
    from crawler.editorial import assess, rank_candidates

    technical = """The interpreter uses a stack.
[Source](https://github.com/example/tool)
Reproduce it with `uv run main.py`.
Expected output is 7; the failure case returns exit code 1.
"""
    cards = [
        assess(
            NewsItem(
                title="GPT launch",
                url="https://openai.com/news",
                source="HackerNews",
                score=9000,
            ),
            "We announce a new model. "
            + "[GitHub](https://github.com/example/tool)\n```python\npass\n```" * 20,
        ),
        assess(
            NewsItem(
                title="Small LLM experiment",
                url="https://simonwillison.net/notes",
                source="Simon Willison",
                score=1,
            ),
            technical,
        ),
        assess(
            NewsItem(
                title="LLM recipe",
                url="https://openai.com/recipe",
                source="HackerNews",
                score=9000,
            ),
            technical,
        ),
    ]
    ranked = rank_candidates(cards)
    assert ranked[0].item.title == "Small LLM experiment"
    assert ranked[1].publisher_role == "vendor"
    assert ranked[2].implementation_potential == "none"
    assert ranked[2].content_types == ["news"]


def test_missing_materials_and_fetch_failure_are_explicit():
    from crawler.editorial import assess

    item = NewsItem(
        title="Claude reproducibility",
        url="https://example.org/post",
        source="HackerNews",
        score=9999,
    )
    missing = assess(
        item,
        "Run `uv run main.py` to reproduce.\nExpected output: 7.\nA benchmark compares a baseline.",
    )
    assert missing.implementation_potential == "needs_materials"
    assert "research" in missing.content_types and "technical" in missing.content_types
    assert missing.gaps and not missing.case_idea
    failed = assess(item, None, fetch_status="timeout", fetch_error="30s")
    assert failed.fetch_status == "timeout" and failed.fetch_error == "30s"
    assert not failed.evidence and not failed.materials and failed.priority == 0
    assert failed.implementation_potential == "none"


def test_real_reproduction_clues_produce_only_an_unrun_proposal():
    from crawler.editorial import assess

    item = NewsItem(
        title="KillSwitch: a language LLMs cannot use",
        url="https://blog.example/killswitch",
        source="HackerNews",
        score=1,
    )
    body = """The interpreter executes statements in reverse order using a stack.
Source code: [interpreter](https://github.com/example/killswitch).
To reproduce the example, run `cargo run -- run example.ks`.
Expected output is 7. A failure case uses the wrong execution order and an empty stack.
"""
    card = assess(item, body)
    assert card.implementation_potential == "proposal"
    assert card.experiment_status == "not_run"
    assert {e.kind for e in card.evidence} >= {"reproduction", "outcome", "mechanism"}
    assert all(e.excerpt in body for e in card.evidence)
    assert card.materials == ["https://github.com/example/killswitch"]
    assert card.case_idea and card.requirements and card.acceptance and card.question

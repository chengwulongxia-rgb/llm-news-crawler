"""No browser, no linked-code execution; bounded fetches with explicit failures."""

import asyncio
import hashlib

import httpx


def test_article_body_selector_excludes_recommendations(monkeypatch):
    from crawler import evidence_fetch as fetch

    html = (
        '<main><div class="rich-lfc-content"><p>'
        + "The parser follows an explicit execution order. " * 10
        + "</p></div><section>Related launch: reproduce the output with python other.py</section></main>"
    )
    monkeypatch.setattr(fetch, "_is_public_article_url", lambda url: True)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, text=html, headers={"Content-Type": "text/html"}
                )
            )
        ) as client:
            return await fetch.fetch_document("https://public.example/article", client)

    result = asyncio.run(scenario())
    assert result.status == "ok" and "parser" in result.body
    assert "Related launch" not in result.body


def test_html_becomes_bounded_text_with_links_but_not_scripts(monkeypatch):
    from crawler import evidence_fetch as fetch

    html = (
        "<nav>unrelated launch</nav><main><p>The interpreter uses a stack. "
        + "Mechanism explanation. " * 20
        + '</p><p>Reproduce with <code>uv run main.py</code>.</p><a href="https://github.com/example/tool">Source code</a><script>send_secrets()</script></main>'
    )
    monkeypatch.setattr(fetch, "_is_public_article_url", lambda url: True)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, text=html, headers={"Content-Type": "text/html"}
                )
            )
        ) as client:
            return await fetch.fetch_document("https://public.example/article", client)

    result = asyncio.run(scenario())
    assert result.status == "ok"
    assert "https://github.com/example/tool" in result.body
    assert "uv run main.py" in result.body
    assert "send_secrets" not in result.body and "unrelated launch" not in result.body
    assert result.sha256 == hashlib.sha256(result.body.encode()).hexdigest()


def test_oversized_or_nontext_response_is_not_evidence(monkeypatch):
    from crawler import evidence_fetch as fetch

    monkeypatch.setattr(fetch, "_is_public_article_url", lambda url: True)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=b"x" * 500, headers={"Content-Type": "text/plain"}
                )
            )
        ) as client:
            big = await fetch.fetch_document(
                "https://public.example/article", client, max_bytes=100
            )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b"fake pdf",
                    headers={"Content-Type": "application/pdf"},
                )
            )
        ) as client:
            pdf = await fetch.fetch_document("https://public.example/article", client)
        return big, pdf

    big, pdf = asyncio.run(scenario())
    assert big.status == "too_large" and not big.body
    assert pdf.status == "unsupported_type" and not pdf.body


def test_redirect_to_private_url_is_never_requested(monkeypatch):
    from crawler import evidence_fetch as fetch

    requested = []

    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})

    monkeypatch.setattr(
        fetch, "_is_public_article_url", lambda url: not url.startswith("http://127.")
    )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await fetch.fetch_document("https://public.example/article", client)

    result = asyncio.run(scenario())
    assert result.status == "blocked_url"
    assert requested == ["https://public.example/article"]
    assert not result.body

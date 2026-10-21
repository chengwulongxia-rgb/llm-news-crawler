"""Bounded HTTP-only discovery fetches. Never executes JS or follows material links.

Every redirect gets a public-DNS precheck, but this is not protection against DNS
rebinding. Run the crawler on a host without access to sensitive internal services.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from crawler.sources.ai_security_news import _is_public_article_url


@dataclass
class Document:
    status: str
    body: str = ""
    error: str = ""
    url: str = ""
    sha256: str = ""
    truncated: bool = False


async def fetch_document(
    url: str,
    client: httpx.AsyncClient,
    *,
    timeout: float = 20,
    max_bytes: int = 1_000_000,
) -> Document:
    """Fetch a single page without running browser JS; redirects are explicit."""
    try:
        async with asyncio.timeout(timeout):
            current = url
            for _ in range(5):
                if not await asyncio.to_thread(_is_public_article_url, current):
                    return Document(
                        "blocked_url",
                        error="URL/DNS not public or resolution failed",
                        url=current,
                    )
                async with client.stream(
                    "GET", current, follow_redirects=False, timeout=timeout,
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    encoding = response.headers.get("content-encoding", "identity")
                    if encoding.strip().lower() != "identity":
                        return Document(
                            "unsupported_encoding", error=encoding, url=current
                        )
                    if response.is_redirect:
                        current = urljoin(current, response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
                    if content_type not in {
                        "text/html",
                        "text/plain",
                        "text/markdown",
                        "application/xhtml+xml",
                    }:
                        return Document(
                            "unsupported_type",
                            error=content_type or "missing content-type",
                            url=current,
                        )
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > max_bytes:
                            return Document(
                                "too_large",
                                error=f"response exceeds {max_bytes} bytes",
                                url=current,
                            )
                    text = chunks.decode("utf-8", errors="replace")
                    if "html" in content_type:
                        soup = BeautifulSoup(text, "lxml")
                        for node in soup.select(
                            "script, style, nav, header, footer, aside, noscript"
                        ):
                            node.decompose()
                        root = None
                        for selector in (
                            "[itemprop='articleBody']",
                            ".rich-lfc-content",
                            ".entry-content",
                            ".post-content",
                            ".markdown-body",
                            ".entryPage",
                            "article",
                            "main",
                        ):
                            if (root := soup.select_one(selector)) is not None:
                                break
                        if root is None:
                            root = soup.body or soup
                        for anchor in root.select("a[href]"):
                            target = urljoin(current, str(anchor.get("href", "")))
                            anchor.replace_with(
                                f"{anchor.get_text(' ', strip=True)} ({target})"
                            )
                        text = root.get_text("\n", strip=True)
                    if len(text.strip()) < 200 or (
                        len(text) < 1500
                        and any(
                            marker in text.lower()
                            for marker in (
                                "verify you are human",
                                "enable javascript",
                                "subscribe to continue",
                                "access denied",
                                "just a moment",
                            )
                        )
                    ):
                        return Document(
                            "unavailable",
                            error="too short or access-wall text; not article evidence",
                            url=current,
                        )
                    truncated = len(text) > 75000
                    text = text[:75000]
                    return Document(
                        "ok",
                        body=text,
                        url=current,
                        sha256=hashlib.sha256(text.encode()).hexdigest(),
                        truncated=truncated,
                    )
            return Document(
                "redirect_limit", error="more than 4 redirects", url=current
            )
    except TimeoutError:
        return Document("timeout", error=f"page exceeded {timeout}s", url=url)
    except (httpx.HTTPError, ValueError) as exc:
        return Document("http_error", error=str(exc), url=url)

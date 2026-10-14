"""Targeted Google News RSS monitor for significant AI-security incidents."""

import asyncio
import ipaddress
import logging
import re
import socket
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

import httpx
from googlenewsdecoder import gnewsdecoder

from crawler.models import NewsItem

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?"
logger = logging.getLogger(__name__)
SEARCHES = (
    ('"AI agent" ("data breach" OR cyberattack OR vulnerability)', "en-US", "US"),
    ('"AI-powered" ("data breach" OR cyberattack OR vulnerability)', "en-US", "US"),
    ('"agente de IA" ("brecha de datos" OR ciberataque OR vulnerabilidad)', "es", "ES"),
)

AI_TERMS = (
    "ai agent", "agentic", "ai-powered", "artificial intelligence", "llm",
    "large language model", "agente de ia", "inteligencia artificial",
)
INCIDENT_TERMS = (
    "data breach", "personal data", "cyberattack", "cyber attack", "vulnerability",
    "exploit", "hacked", "ransomware", "brecha de datos", "datos personales",
    "ciberataque", "vulnerabilidad", "filtración",
)
# This feed supplies incident *candidates*; exclude explicit advice/explainers
# before sending candidates to the cron agent for full-text verification.
NON_INCIDENT_TITLE = re.compile(
    r"^(?:how to\b|guide to\b|what is\b|tips for\b|explainer\b|guía\b|cómo\b)"
    r"|\b(?:guide|checklist|best practices|notification clause)\b",
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    """Remove the trailing publisher name added by Google News."""
    return re.sub(r"\s+-\s+[^-]+$", "", unescape(title)).strip()


def _is_ai_security_incident(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    return (
        not NON_INCIDENT_TITLE.search(title)
        and any(term in text for term in AI_TERMS)
        and any(term in text for term in INCIDENT_TERMS)
    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _is_public_article_url(url: str) -> bool:
    """Best-effort check of URL and current DNS; redirects/rebinding need fetch-time checks."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            if "." not in host:
                return False
            try:
                addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            except OSError as exc:
                logger.warning("Article hostname DNS lookup failed (%s): %s", host, exc)
                return False
            return bool(addresses) and all(
                ipaddress.ip_address(result[4][0]).is_global for result in addresses
            )
    except ValueError:
        return False


def resolve_google_news_url(url: str) -> str | None:
    """Resolve a Google News URL to a public-looking publisher URL."""
    try:
        decoded = gnewsdecoder(url)
    except Exception as exc:  # noqa: BLE001 - third-party decoder raises several error types
        logger.warning("Google News URL decode failed (%s): %s", url, exc)
        return None
    target = decoded.get("decoded_url") if decoded.get("status") else None
    if not isinstance(target, str) or not _is_public_article_url(target):
        logger.warning("Google News URL did not decode to a public article: %s", url)
        return None
    return target


def parse_google_news_rss(content: bytes, source_name: str) -> list[NewsItem]:
    """Parse and retain only news whose title/summary signals an AI incident."""
    root = ElementTree.fromstring(content)
    items: list[NewsItem] = []
    for node in root.findall("./channel/item"):
        raw_title = node.findtext("title") or ""
        title = _clean_title(raw_title)
        url = (node.findtext("link") or "").strip()
        summary = re.sub(r"<[^>]+>", " ", node.findtext("description") or "")
        summary = re.sub(r"\s+", " ", unescape(summary)).strip()
        if not title or not url or not _is_ai_security_incident(title, summary):
            continue
        published_at = _parse_date(node.findtext("pubDate"))
        if published_at is None:
            logger.warning("AI security RSS item lacks a valid date: %s", title)
            continue
        items.append(NewsItem(
            title=title,
            url=url,
            source=source_name,
            score=50,
            published_at=published_at,
            summary=summary,
        ))
    return items


async def fetch_ai_security_news(
    client: httpx.AsyncClient, limit_per_search: int = 10
) -> list[NewsItem]:
    """Fetch recent incident headlines from three English/Spanish Google News searches."""
    collected: list[NewsItem] = []
    successful_searches = 0
    candidates_to_decode = 0
    for query, language, country in SEARCHES:
        params = urlencode({"q": query, "hl": language, "gl": country, "ceid": f"{country}:{language}"})
        try:
            response = await client.get(f"{GOOGLE_NEWS_RSS}{params}", follow_redirects=True)
            response.raise_for_status()
            parsed = parse_google_news_rss(response.content, "AI Security Watch")[:limit_per_search]
            successful_searches += 1
            candidates_to_decode += len(parsed)
            for item in parsed:
                resolved = await asyncio.to_thread(resolve_google_news_url, item.url)
                if resolved is None:
                    continue
                item.url = resolved
                collected.append(item)
        except (httpx.HTTPError, ElementTree.ParseError) as exc:
            logger.warning("AI security RSS query failed (%s): %s", query, exc)

    if successful_searches == 0:
        raise RuntimeError("all AI security RSS searches failed")
    if candidates_to_decode > 0 and not collected:
        raise RuntimeError("all AI security RSS candidate URLs failed to decode safely")

    unique: dict[str, NewsItem] = {}
    for item in collected:
        unique.setdefault(item.url, item)
    return list(unique.values())

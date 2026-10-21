"""Offline digest assembly. Source text is data, never executable instructions."""

import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from crawler.editorial import Candidate

TAIPEI = ZoneInfo("Asia/Taipei")
COLLECTOR_LINE = re.compile(r"^• (.*?) — (.*?) — 來源: (\S+)$")


def _dated_files(directory: Path, now: datetime, days: int, suffix: str) -> list[Path]:
    return [
        path
        for offset in range(days)
        for path in sorted(
            directory.glob(
                f"{(now - timedelta(days=offset)).date().isoformat()}*{suffix}"
            )
        )
        if path.is_file()
    ]


def _valid_url(url: str) -> bool:
    """Check syntax only: no repair, DNS lookup, fetching or event inference."""
    try:
        parts = urlsplit(url)
        return bool(
            parts.scheme in {"https", "http"}
            and parts.hostname
            and not any(
                ch.isspace() or unicodedata.category(ch).startswith("C") or ch in "<>"
                for ch in url
            )
        )
    except ValueError:
        return False


def build_manifest(
    collector_dir: Path, candidate_dir: Path, now: datetime, days: int = 2
) -> dict:
    """Read calendar-day batches and assign structure without model decisions."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if type(days) is not int or days < 1:
        raise ValueError("days must be a positive integer")
    local = now.astimezone(TAIPEI)
    warnings = []
    excluded = []
    entries = {}
    for path in _dated_files(collector_dir, local, days, ".md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = COLLECTOR_LINE.fullmatch(line)
            if match:
                title, summary, url = match.groups()
                entries.setdefault(
                    url,
                    {"title": title, "summary": summary, "url": url, "candidate": None},
                )
    cards = {}
    publication = {}
    for path in _dated_files(candidate_dir, local, days, ".json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(report, dict) or not isinstance(
                report.get("candidates"), list
            ):
                raise TypeError("expected object with candidates list")
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            warnings.append(f"{path.name}: 無法解析候選 JSON：{exc}")
            continue
        try:
            stamp = datetime.fromisoformat(report.get("generated_at", ""))
            if stamp.tzinfo is None:
                raise ValueError("generated_at missing timezone")
            generated = stamp.timestamp()
        except (TypeError, ValueError):
            warnings.append(f"{path.name}: 無效 generated_at；不推測時間")
            generated = float("-inf")
        for index, card in enumerate(report["candidates"]):
            try:
                parsed = Candidate.model_validate(card)
            except ValueError as exc:
                warnings.append(f"{path.name} candidate[{index}]: 無效候選：{exc}")
                continue
            url = parsed.item.url
            if not _valid_url(url):
                reason = "無效 URL（原 token 保留，不修復）"
                warnings.append(f"{reason}：{url}")
                excluded.append({"url": url, "reason": reason})
                continue
            card = dict(card, priority=parsed.priority)
            card.setdefault("fetch_status", parsed.fetch_status)
            card.setdefault("evidence", [])
            quality = (
                parsed.fetch_status == "ok",
                bool(parsed.evidence),
                generated,
                len(parsed.evidence),
            )
            if url not in cards or quality > cards[url][0]:
                cards[url] = (quality, card)
                publication[url] = parsed.item.published_at
    for url, (_, card) in cards.items():
        if url in entries:
            entries[url]["candidate"] = card
        elif card["fetch_status"] == "ok" and card["evidence"]:
            item = card["item"]
            entries[url] = {
                "title": item["title"],
                "summary": item.get("summary", ""),
                "url": url,
                "candidate": card,
            }
    first_day = local.date() - timedelta(days=days - 1)
    for url, entry in list(entries.items()):
        if not _valid_url(url):
            reason = "無效 URL（原 token 保留，不修復）"
            warnings.append(f"{reason}：{url}")
            if not any(record["url"] == url for record in excluded):
                excluded.append({"url": url, "reason": reason})
            del entries[url]
            continue
        dates = []
        for match in re.finditer(
            r"/(\d{4})/(\d{2})/(\d{2})(?:/|$)", urlsplit(url).path
        ):
            try:
                dates.append(date(*(int(part) for part in match.groups())))
            except ValueError:
                warnings.append(f"URL 日期無法解析（不推測）：{url}")
        month_names = {
            name.lower(): number
            for number, name in enumerate(
                [
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                    "Oct",
                    "Nov",
                    "Dec",
                ],
                1,
            )
        }
        for match in re.finditer(
            r"/(\d{4})/([A-Za-z]{3})/(\d{1,2})(?:/|$)", urlsplit(url).path
        ):
            if month := month_names.get(match.group(2).lower()):
                try:
                    dates.append(date(int(match.group(1)), month, int(match.group(3))))
                except ValueError:
                    warnings.append(f"URL 日期無法解析（不推測）：{url}")
        for match in re.finditer(
            r"(?:^|[/-])(\d{4})-(\d{2})-(\d{2})(?:$|[/.])", urlsplit(url).path
        ):
            try:
                dates.append(date(*(int(part) for part in match.groups())))
            except ValueError:
                warnings.append(f"URL 日期無法解析（不推測）：{url}")
        stamp = publication.get(url)
        if stamp is not None:
            if stamp.tzinfo is not None:
                dates.append(stamp.astimezone(TAIPEI).date())
            else:
                warnings.append(f"發表時間缺時區（不推測）：{url}")
        outside = [day for day in dates if not first_day <= day <= local.date()]
        if outside:
            reason = (
                f"發表日期 {', '.join(day.isoformat() for day in outside)} "
                f"不在近 {days} 曆日 {first_day} 至 {local.date()}；批次日期不是發表日期"
            )
            warnings.append(f"{url}：{reason}")
            excluded.append(
                {
                    "url": url,
                    "reason": reason,
                    "kind": "old_article"
                    if all(day < first_day for day in outside)
                    else "outside_window",
                }
            )
            del entries[url]
    preference = {
        "independent": 3,
        "community_discovery": 2,
        "community": 2,
        "research": 1,
    }
    ordered = sorted(
        entries.values(),
        key=lambda entry: (
            -(entry["candidate"] or {}).get("priority", 0),
            -preference.get((entry["candidate"] or {}).get("publisher_role", ""), 0),
        ),
    )
    for index, entry in enumerate(ordered, 1):
        entry["id"] = str(index)
    return {
        "generated_at": local.isoformat(),
        "days": days,
        "entries": ordered,
        "warnings": warnings,
        "excluded": excluded,
    }


URL_TOKEN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
SUMMARY_URL = re.compile(r"\b(?:[a-z][\w+.-]*://|www\.|mailto:)", re.IGNORECASE)
STRUCTURE = re.compile(r"#\s*\d|來源\s*[:：]|共\s*\d+\s*則|^\s*\d+[.)、]")
CONTENT_TYPES = {
    "news": "新聞",
    "technical": "技術",
    "research": "研究",
    "policy": "政策",
    "incident": "資安事件",
    "roundup": "彙整",
}


def _single_line(value: str) -> str:
    """Defang presentation controls in untrusted prose, not the original URL."""
    text = "".join(
        " " if unicodedata.category(ch).startswith("C") or ch.isspace() else ch
        for ch in str(value)
    )
    text = URL_TOKEN.sub("［連結省略］", text)
    text = re.sub(r"來源\s*[:：]", "來源線索：", text)
    return " ".join(text.replace("#", "＃").split())


def _header(manifest: dict) -> str:
    stamp = datetime.fromisoformat(manifest["generated_at"])
    if stamp.tzinfo is None:
        raise ValueError("generated_at must have timezone")
    return f"# 新聞匯總｜{stamp.astimezone(TAIPEI):%Y-%m-%d %H:%M:%S}（Asia/Taipei）"


def render_digest(manifest: dict, summaries: dict[str, str]) -> str:
    """Render only exact, validated model summaries; structure is program-owned.

    Summaries are limited to 280 characters. Missing/invalid values are errors,
    never replaced by plausible prose or by a source's untrusted instructions.
    """
    entries = manifest["entries"]
    ids = [entry["id"] for entry in entries]
    if not isinstance(summaries, dict) or set(summaries) != set(ids):
        raise ValueError("summaries must contain exactly all manifest entry ids")
    for summary in summaries.values():
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > 280
            or any(
                unicodedata.category(ch).startswith("C") or ch in "\u2028\u2029"
                for ch in summary
            )
            or SUMMARY_URL.search(summary)
            or STRUCTURE.search(summary)
        ):
            raise ValueError(
                "summary must be short single-line prose without URLs or structure"
            )
    lines = [
        _header(manifest),
        f"共 {len(entries)} 則（近 {manifest['days']} 曆日）",
        "",
    ]
    if not entries:
        lines.append(f"近 {manifest['days']} 曆日無新聞。")
    for entry in entries:
        card = entry["candidate"] or {}
        labels = [
            CONTENT_TYPES.get(label, label) for label in card.get("content_types", [])
        ]
        evidence = card.get("evidence", [])
        gaps = "；".join(card.get("gaps", []))
        lines.extend(
            [
                f"## #{entry['id']} {_single_line(entry['title'])}",
                f"摘要：{summaries[entry['id']]}",
            ]
        )
        if card:
            lines.extend(
                [
                    f"分類：{_single_line('／'.join(labels)) or '未分類'}",
                ]
            )
        if gaps and card.get("implementation_potential") in {
            "proposal",
            "needs_materials",
        }:
            lines.append(f"待補材料：{_single_line(gaps)[:100]}")
        if (
            card.get("fetch_status") == "ok"
            and evidence
            and card.get("implementation_potential") == "proposal"
            and card.get("case_idea")
        ):
            lines.append(f"案例提案（待驗證）：{_single_line(card['case_idea'])}")
        lines.extend([f"來源: {entry['url']}", ""])
    handled = {
        f"{record['url']}：{record['reason']}"
        for record in manifest.get("excluded", [])
        if record.get("kind") == "old_article"
    }
    old_count = len(
        {
            record["url"]
            for record in manifest.get("excluded", [])
            if record.get("kind") == "old_article"
        }
    )
    if old_count:
        lines.append(f"處理摘要：已排除 {old_count} 篇舊文。")
    for warning in dict.fromkeys(manifest["warnings"]):
        if warning not in handled:
            lines.append(f"資料異常：{_single_line(warning)}")
    lines.extend(
        [
            "",
            "註：近兩日指收集時間；這是選題摘要，非查證結論；案例尚未實作。",
            "城武，哪幾篇要深入分析？給我編號就好。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def validate_digest(text: str, manifest: dict) -> None:
    """Reject drift in clock, totals, ids or exact source URL order/uniqueness."""
    entries = manifest["entries"]
    expected_ids = [str(index) for index in range(1, len(entries) + 1)]
    if [entry["id"] for entry in entries] != expected_ids:
        raise ValueError("manifest ids are not contiguous")
    urls = [entry["url"] for entry in entries]
    if len(urls) != len(set(urls)):
        raise ValueError("manifest URLs must be unique")
    lines = text.splitlines()
    if not lines or lines[0] != _header(manifest):
        raise ValueError("digest header time does not match generated_at")
    count_lines = [line for line in lines if re.match(r"^共\s*\d+\s*則", line)]
    if count_lines != [f"共 {len(entries)} 則（近 {manifest['days']} 曆日）"]:
        raise ValueError("declared count does not match manifest")
    actual_ids = re.findall(r"^## #(\d+) .*$", text, re.MULTILINE)
    prose = URL_TOKEN.sub("", text)
    if re.findall(r"#\s*(\d+)", prose) != expected_ids:
        raise ValueError("extra or forged numbered entry")
    if actual_ids != expected_ids:
        raise ValueError("digest ids must be contiguous and match manifest")
    source_lines = [line for line in lines if line.startswith("來源:")]
    if source_lines != [f"來源: {url}" for url in urls]:
        raise ValueError("source URLs do not match manifest order/count")
    if URL_TOKEN.findall(text) != urls:
        raise ValueError("extra, missing, reordered or duplicate URL")

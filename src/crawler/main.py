#!/usr/bin/env python3
"""LLM News Crawler — 龍蝦城武新聞蒐集器 CLI.

Usage:
    uv run llm-crawler                    # 執行蒐集，輸出到 stdout
    uv run llm-crawler --json              # 輸出 JSON
    uv run llm-crawler --min-score 10      # 只保留 >= 10 分的
    uv run llm-crawler --no-dedup          # 停用跨輪次去重
    uv run llm-crawler --clear-dedup       # 清除去重記錄
    uv run llm-crawler -o /tmp/news.txt    # 輸出到檔案
    uv run llm-crawler --fetch URL         # 擷取文章內文
    uv run llm-crawler --fetch-fallback URL  # 擷取失敗時搜尋替代來源
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

import httpx

from crawler.sources.hackernews import fetch_hackernews
from crawler.sources.rss_feeds import fetch_all_rss
from crawler.sources.sitemap_blogs import fetch_all_sitemaps
from crawler.sources.playwright_blogs import fetch_all_playwright
from crawler.filters import filter_items
from crawler.models import NewsItem
from crawler.dedup import DedupStore
from crawler.article_fetcher import fetch_article, get_last_fetch_error, FetchError


# ── Error code mapping for user-friendly messages ──────────────────

ERROR_MESSAGES = {
    FetchError.PAYWALL:    "付費牆（需要訂閱或登入）",
    FetchError.CF_BLOCK:   "Cloudflare / 機器人防護阻擋",
    FetchError.TIMEOUT:    "請求逾時",
    FetchError.HTTP_ERROR: "HTTP 錯誤",
    FetchError.EMPTY:      "無法擷取內文（頁面可能為空或非文章格式）",
    FetchError.UNKNOWN:    "未知錯誤",
}

ERROR_RECOVERY_HINTS = {
    FetchError.PAYWALL:    "🔍 建議用 --fetch-fallback 自動搜尋替代報導",
    FetchError.CF_BLOCK:   "💡 可嘗試用 --playwright 繞過 JS 挑戰",
    FetchError.TIMEOUT:    "💡 可增加 timeout 或檢查網路連線",
    FetchError.HTTP_ERROR: "💡 檢查 URL 是否正確、網站是否存活",
    FetchError.EMPTY:      "💡 頁面可能需要 JavaScript 渲染，試 --playwright",
    FetchError.UNKNOWN:    "💡 用 --debug 查看詳細錯誤",
}


def format_error_output(err: FetchError, detail: str, url: str) -> str:
    """Format a user-friendly error message for fetch failures."""
    msg = ERROR_MESSAGES.get(err, "未知原因")
    hint = ERROR_RECOVERY_HINTS.get(err, "")
    parts = [f"❌ 無法擷取文章：{url}", f"   原因：{msg}"]
    if detail:
        parts.append(f"   細節：{detail}")
    if hint:
        parts.append(f"   {hint}")
    return "\n".join(parts)


from dataclasses import dataclass, field


@dataclass
class DateFilterReport:
    """Report from filter_by_date about what was filtered and why."""
    no_date_items: list[str] = field(default_factory=list)  # titles of items with no date
    old_items_dropped: list[tuple[str, str]] = field(default_factory=list)  # (title, date) of dropped items


def format_collector_output(items: list[NewsItem], date_str: str, date_report: DateFilterReport | None = None) -> str:
    """Format items in collector-compatible format, with optional date warnings."""
    if not items:
        out = f"=== {date_str} 新聞 ===\n本次無新資訊"
    else:
        lines = [f"=== {date_str} 新聞 ==="]
        for item in items:
            lines.append(item.to_collector_line())
        out = "\n".join(lines)

    # Append date filter warnings so the digest agent can flag them
    if date_report and (date_report.no_date_items or date_report.old_items_dropped):
        warnings = []
        if date_report.no_date_items:
            names = "、".join(date_report.no_date_items[:5])
            warnings.append(f"⚠️ 日期不明（無法判斷新舊，已保留）：{names}")
        if date_report.old_items_dropped:
            entries = [f"{title}（{date}）" for title, date in date_report.old_items_dropped[:5]]
            warnings.append(f"📅 已自動移除舊聞：{'；'.join(entries)}")
        out += "\n\n" + "\n".join(warnings)

    return out


def filter_by_date(items: list[NewsItem], max_age_days: int = 7) -> tuple[list[NewsItem], DateFilterReport]:
    """Filter items to only those published within max_age_days.

    Items without a published_at date are kept (we can't determine age),
    but flagged in the returned report.

    Returns (filtered_items, report).
    """
    report = DateFilterReport()

    if max_age_days <= 0:
        return items, report

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)

    kept = []
    for item in items:
        if item.published_at is None:
            # No date info — keep it but flag
            kept.append(item)
            report.no_date_items.append(item.title or item.url)
            continue

        pub = item.published_at
        # Normalize to UTC-aware for comparison
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        else:
            pub = pub.astimezone(timezone.utc)

        if pub >= cutoff:
            kept.append(item)
        else:
            date_str = pub.strftime("%Y-%m-%d")
            report.old_items_dropped.append((item.title or item.url, date_str))

    if report.old_items_dropped:
        print(f"📅 日期過濾：移除 {len(report.old_items_dropped)} 則超過 {max_age_days} 天的舊聞", file=sys.stderr)

    return kept, report


async def run(
    sources: list[str],
    min_score: int = 5,
    max_age_days: int = 7,
    dedup_store: DedupStore | None = None,
) -> tuple[list[NewsItem], DateFilterReport]:
    """Run all enabled sources concurrently."""
    all_items: list[NewsItem] = []

    async with httpx.AsyncClient() as client:
        tasks = []
        if "hackernews" in sources:
            tasks.append(fetch_hackernews(client))
        if "rss" in sources:
            tasks.append(fetch_all_rss(client))
        if "sitemap" in sources:
            tasks.append(fetch_all_sitemaps(client))
        if "playwright" in sources:
            tasks.append(fetch_all_playwright())

        if tasks:
            results = await asyncio.gather(*tasks)
            for result in results:
                all_items.extend(result)

    # Filter by LLM relevance and minimum score
    filtered = filter_items(all_items, min_score=min_score)

    # Filter by publication date (keep only recent items)
    filtered, date_report = filter_by_date(filtered, max_age_days=max_age_days)

    # In-run deduplication by URL
    seen_urls_in_run = set()
    unique_items = []
    for item in filtered:
        if item.url not in seen_urls_in_run:
            seen_urls_in_run.add(item.url)
            unique_items.append(item)

    # Cross-run deduplication
    # HN high-score items (>100 points) bypass dedup — a hot HN story
    # should not be hidden just because a lower-signal source saw the URL first.
    if dedup_store is not None:
        kept = []
        for item in unique_items:
            if dedup_store.is_seen(item.url):
                # Allow through if it's a high-signal HN story
                if item.source == "HackerNews" and item.score > 100:
                    dedup_store.mark_seen(item.url)  # refresh TTL
                    kept.append(item)
                # else: silently dropped (normal dedup)
            else:
                kept.append(item)
        unique_items = kept

    # Sort by score descending
    unique_items.sort(key=lambda x: x.score, reverse=True)

    # Mark as seen for future runs
    if dedup_store is not None:
        dedup_store.mark_seen_batch([item.url for item in unique_items])

    return unique_items, date_report


def _build_fallback_search_query(url: str, article_title: str = "") -> str:
    """Build a web search query to find alternative coverage of a story.

    Extracts keywords from URL path segments, prioritizing
    longer/more meaningful words.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path.strip("/")

    # Split path into segments and extract words
    all_words = []
    for segment in path.split("/"):
        # Replace hyphens, underscores with spaces
        cleaned = segment.replace("-", " ").replace("_", " ")
        for word in cleaned.split():
            if len(word) > 2 and word.lower() not in ("the", "and", "for", "with", "are", "was", "has", "its",
                                                       "www", "com", "org", "net", "htm", "html", "php", "asp",
                                                       "this", "that", "from", "his", "her", "our", "you", "not",
                                                       "can", "all"):
                all_words.append(word)

    # Deduplicate while preserving order
    seen = set()
    unique_words = []
    for w in all_words:
        if w.lower() not in seen:
            seen.add(w.lower())
            unique_words.append(w)

    # Use up to 8 keywords
    query = " ".join(unique_words[:8])
    if not query:
        query = parsed.hostname.replace("www.", "").replace(".com", "") if parsed.hostname else ""
    return query


async def _fetch_with_fallback(url: str, debug: bool = False) -> str | None:
    """Attempt to fetch an article; if it fails with PAYWALL,
    return a structured fallback note with suggested search queries
    for the caller to find alternative coverage.

    Returns markdown content or None if completely failed.
    """
    from crawler.article_fetcher import fetch_article as _fa, get_last_fetch_error as _gle, FetchError as _FE
    result = await _fa(url, debug=debug)

    if result is not None:
        return result.to_markdown()

    err, detail = _gle()
    if err != _FE.PAYWALL:
        # Not a paywall — let caller handle
        return None

    # Paywall detected — build fallback note with search suggestions
    query = _build_fallback_search_query(url)
    if debug:
        print(f"[fallback] Paywall detected for {url}", file=sys.stderr)
        print(f"[fallback] Suggested search: {query}", file=sys.stderr)

    fallback_note = f"""# (付費牆替代彙整)

> ⚠️ 原文位於付費牆後，無法直接擷取。
> 原文網址：{url}
> 
> 建議搜尋關鍵詞：**{query}**
>
> 請使用 web_search 工具搜尋同一事件的不同來源報導，聚合多篇替代文章。

---

*此為自動產生的付費牆提示。請以搜尋結果中的替代來源為準。*
"""
    return fallback_note


def main():
    parser = argparse.ArgumentParser(
        description="龍蝦城武 LLM 新聞爬蟲",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sources", nargs="+", default=["hackernews", "rss", "sitemap", "playwright"],
        help="新聞來源: hackernews, rss, sitemap, playwright (default: all)",
    )
    parser.add_argument(
        "--min-score", type=int, default=5,
        help="最低分數門檻 (default: 5)",
    )
    parser.add_argument(
        "--max-age-days", type=int, default=7,
        help="只保留 N 天內發布的新聞 (default: 7, 0=不過濾)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="輸出 JSON 格式",
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="輸出到檔案 (預設 stdout)",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="最多輸出幾則 (default: 10)",
    )
    parser.add_argument(
        "--no-dedup", action="store_true",
        help="停用跨輪次 URL 去重",
    )
    parser.add_argument(
        "--clear-dedup", action="store_true",
        help="清除所有去重記錄後退出",
    )
    parser.add_argument(
        "--dedup-stats", action="store_true",
        help="顯示去重統計後退出",
    )
    parser.add_argument(
        "--fetch", type=str, default=None, metavar="URL",
        help="擷取單篇文章內文 (輸出 markdown)",
    )
    parser.add_argument(
        "--playwright", action="store_true", dest="force_playwright",
        help="配合 --fetch，強制使用 Playwright",
    )
    parser.add_argument(
        "--fetch-smart", type=str, default=None, metavar="URL",
        help="智慧擷取：自動辨識 arXiv/HN/YouTube/一般網站，用最佳方式擷取內文",
    )
    parser.add_argument(
        "--fetch-fallback", type=str, default=None, metavar="URL",
        help="擷取文章；若遇付費牆則自動搜尋替代來源彙整",
    )
    parser.add_argument(
        "--fetch-batch", type=str, default=None, metavar="FILE",
        help="批次擷取：從檔案讀取 URL 清單（每行一個），平行擷取所有文章",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="顯示 fetch 失敗的詳細原因（stderr）",
    )

    args = parser.parse_args()

    # Handle dedup store commands
    dedup_store = None if args.no_dedup else DedupStore()

    if args.clear_dedup:
        if dedup_store:
            dedup_store.clear()
        print("✅ 已清除所有去重記錄", file=sys.stderr)
        return

    if args.dedup_stats:
        if dedup_store:
            stats = dedup_store.stats()
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    # ── Fetch single article mode ──
    if args.fetch:
        article = asyncio.run(fetch_article(
            args.fetch,
            force_playwright=args.force_playwright,
            debug=args.debug,
        ))
        if article is None:
            err, detail = get_last_fetch_error()
            if err:
                print(format_error_output(err, detail, args.fetch), file=sys.stderr)
            else:
                print(f"❌ 無法擷取文章：{args.fetch}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(article.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(article.to_markdown())
        return

    # ── Smart fetch mode (auto-detect site type) ──
    if args.fetch_smart:
        from crawler.hard_fetch import fetch_any
        content = asyncio.run(fetch_any(args.fetch_smart, debug=args.debug))
        if content is None:
            print(f"❌ 無法擷取文章：{args.fetch_smart}", file=sys.stderr)
            sys.exit(1)
        print(content.to_markdown())
        return

    # ── Fetch with fallback mode ──
    if args.fetch_fallback:
        content = asyncio.run(_fetch_with_fallback(args.fetch_fallback, debug=args.debug))
        if content is None:
            err, detail = get_last_fetch_error()
            if err:
                print(format_error_output(err, detail, args.fetch_fallback), file=sys.stderr)
            else:
                print(f"❌ 無法擷取文章（包含替代來源搜尋）：{args.fetch_fallback}", file=sys.stderr)
            sys.exit(1)
        print(content)
        return

    # ── Batch fetch mode ──
    if args.fetch_batch:
        from crawler.hard_fetch import fetch_batch

        # Read URLs from file
        urls = []
        with open(args.fetch_batch) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

        if not urls:
            print("❌ 檔案中沒有 URL", file=sys.stderr)
            sys.exit(1)

        print(f"🔍 開始擷取 {len(urls)} 篇文章...", file=sys.stderr)
        results = asyncio.run(fetch_batch(urls))

        success_count = 0
        for url, content in zip(urls, results):
            if content is None:
                print(f"\n---\n## ❌ 失敗：{url}\n", file=sys.stderr)
            else:
                success_count += 1
                print(f"\n---\n## ✅ {content.title}\n")
                print(content.body)
                print(f"\n*原文：{url}*")

        print(f"\n---\n📊 成功 {success_count}/{len(urls)} 篇", file=sys.stderr)
        return

    # Run async
    items, date_report = asyncio.run(run(args.sources, min_score=args.min_score, max_age_days=args.max_age_days, dedup_store=dedup_store))
    items = items[: args.limit]

    today = datetime.now().strftime("%Y-%m-%d")

    if args.json:
        output = json.dumps(
            [item.model_dump(mode="json") for item in items],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    else:
        output = format_collector_output(items, today, date_report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output + "\n")
        print(f"✅ 已輸出 {len(items)} 則新聞到 {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

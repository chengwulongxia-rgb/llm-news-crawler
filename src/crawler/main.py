#!/usr/bin/env python3
"""LLM News Crawler — 龍蝦城武新聞蒐集器 CLI.

Usage:
    uv run llm-crawler                    # 執行蒐集，輸出到 stdout
    uv run llm-crawler --json              # 輸出 JSON
    uv run llm-crawler --min-score 10      # 只保留 >= 10 分的
    uv run llm-crawler --no-dedup          # 停用跨輪次去重
    uv run llm-crawler --clear-dedup       # 清除去重記錄
    uv run llm-crawler -o /tmp/news.txt    # 輸出到檔案
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime

import httpx

from crawler.sources.hackernews import fetch_hackernews
from crawler.sources.rss_feeds import fetch_all_rss
from crawler.sources.sitemap_blogs import fetch_all_sitemaps
from crawler.sources.playwright_blogs import fetch_all_playwright
from crawler.filters import filter_items
from crawler.models import NewsItem
from crawler.dedup import DedupStore
from crawler.article_fetcher import fetch_article


def format_collector_output(items: list[NewsItem], date_str: str) -> str:
    """Format items in collector-compatible format."""
    if not items:
        return f"=== {date_str} 新聞 ===\n本次無新資訊"

    lines = [f"=== {date_str} 新聞 ==="]
    for item in items:
        lines.append(item.to_collector_line())
    return "\n".join(lines)


async def run(
    sources: list[str],
    min_score: int = 5,
    dedup_store: DedupStore | None = None,
) -> list[NewsItem]:
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

    return unique_items


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
        help="智慧擷取：自動辨識 arXiv/HN/一般網站，用最佳方式擷取內文",
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
    items = asyncio.run(run(args.sources, min_score=args.min_score, dedup_store=dedup_store))
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
        output = format_collector_output(items, today)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output + "\n")
        print(f"✅ 已輸出 {len(items)} 則新聞到 {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
# LLM News Crawler wrapper for Hermes cron job.
# Runs the Python crawler with all sources including Playwright.
set -euo pipefail

PROJECT_DIR="$HOME/projects/llm-news-crawler"
cd "$PROJECT_DIR"

# Ensure deps are installed
uv sync --quiet 2>/dev/null || true

# Ensure Playwright browser is installed (first run only)
uv run playwright install chromium 2>/dev/null || true

# Run crawler — all sources: HN, RSS, Sitemap, Playwright
# --max-age-days 3: 只保留 3 天內發布的文章（無日期者放行）
DISCOVERY_DIR="$PROJECT_DIR/data/deep-dive"
BATCH=$(TZ='Asia/Taipei' date +%Y-%m-%d_%H%M%S)
exec uv run llm-crawler --min-score 10 --max-age-days 3 --limit 20 \
    --deep-dive-output "$DISCOVERY_DIR/$BATCH.json" --deep-dive-budget 12

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
exec uv run llm-crawler --min-score 10 --limit 20

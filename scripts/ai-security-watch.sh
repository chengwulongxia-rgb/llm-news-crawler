#!/usr/bin/env bash
# Optional manual AI-security candidate crawl (the scheduled agent invokes the CLI directly).
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
exec uv run llm-crawler \
  --sources ai-security \
  --min-score 10 \
  --max-age-days 3 \
  --limit 20

#!/usr/bin/env bash
# Code renders and validates the final notice; the model supplies summaries only.
set -euo pipefail
cd "$HOME/projects/llm-news-crawler"
exec uv run llm-digest --job-id e0349bb0f3b1

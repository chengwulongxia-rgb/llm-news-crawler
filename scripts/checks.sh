#!/usr/bin/env bash
# llm-news-crawler 常用工具指令集
# 用法: source scripts/checks.sh 或直接 bash scripts/checks.sh <command>

set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# ─── 抓取 ───────────────────────────────────────────────

# 用 Playwright 抓部落格文章（Cloudflare 可能擋）
fetch-blog() {
    local url="$1"
    uv run llm-crawler --fetch "$url" --playwright --debug -o "/tmp/fetch-$(date +%s).md"
}

# 用 smart fetch 抓 HN 討論串或 arXiv 論文
fetch-smart() {
    local url="$1"
    uv run llm-crawler --fetch-smart "$url" --debug
}

# ─── HN 搜尋 ─────────────────────────────────────────────

# 搜 HN 上的討論串（回傳 story ID）
hn-search() {
    local query="$1"
    local limit="${2:-5}"
    curl -s "https://hn.algolia.com/api/v1/search?query=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$query'''))")&tags=story&hitsPerPage=$limit" \
        | python3 -c "
import json, sys
d = json.load(sys.stdin)
for h in d.get('hits', []):
    print(f\"{h.get('points',0):>5}pts | {h.get('title','')}\")
    print(f\"       https://news.ycombinator.com/item?id={h['objectID']}\")
    print(f\"       {h.get('url','')}\")
    print()
"
}

# 從 HN story ID 抓討論內容
hn-fetch() {
    local story_id="$1"
    uv run llm-crawler --fetch-smart "https://news.ycombinator.com/item?id=$story_id"
}

# ─── arXiv ────────────────────────────────────────────────

# 快速抓 arXiv 論文摘要
arxiv-fetch() {
    local arxiv_id="$1"
    uv run llm-crawler --fetch-smart "https://arxiv.org/abs/$arxiv_id"
}

# ─── Cron 檢查 ────────────────────────────────────────────

# 看今天爬到的所有新聞
cron-today() {
    local collector_id="${1:-90a0d20fbc91}"
    local today="$(date +%Y-%m-%d)"
    echo "=== 蒐集器輸出（$today）==="
    for f in ~/.hermes/cron/output/"$collector_id"/"$today"_*.md; do
        [ -f "$f" ] && echo "--- $(basename "$f") ---" && cat "$f"
    done
}

# 看中午匯總
cron-digest() {
    local digest_id="${1:-e0349bb0f3b1}"
    local today="$(date +%Y-%m-%d)"
    echo "=== 中午匯總（$today）==="
    for f in ~/.hermes/cron/output/"$digest_id"/"$today"_*.md; do
        [ -f "$f" ] && cat "$f"
    done
}

# ─── 網站可達性 ──────────────────────────────────────────

# 測試 URL 是否被 Cloudflare 擋
check-cf() {
    local url="$1"
    local html_len
    html_len=$(curl -sL -o /dev/null -w '%{size_download}' "$url" 2>/dev/null || echo "0")
    if [ "$html_len" -lt 5000 ]; then
        echo "❌ 可能被擋（HTML: ${html_len} bytes）→ $url"
    else
        echo "✅ 可存取（HTML: ${html_len} bytes）→ $url"
    fi
}

# 快速測 Playwright 能不能抓（不透過 crawler，直接用 Playwright 測）
test-playwright() {
    local url="$1"
    uv run python -c "
import asyncio
from playwright.async_api import async_playwright

async def t():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page()
        await page.goto('$url', wait_until='networkidle', timeout=60000)
        html = await page.content()
        print(f'HTML: {len(html)} bytes')
        print(f'Title: {await page.title()}')
        await browser.close()

asyncio.run(t())
" 2>&1
}

# ─── 主入口 ───────────────────────────────────────────────

# 當原站被 Cloudflare 擋，用 HN 討論串當替代來源
# 用法: cf-fallback "https://www.anthropic.com/news/claude-opus-4-8"
cf-fallback() {
    local url="$1"
    echo "=== 原站被 Cloudflare 擋，搜尋替代來源 ==="
    echo ""
    
    # 1. 搜 HN 討論串
    local domain
    domain=$(echo "$url" | python3 -c "from urllib.parse import urlparse; import sys; print(urlparse(sys.stdin.read().strip()).path.split('/')[-1] or urlparse(sys.stdin.read().strip()).path.split('/')[-2])" 2>/dev/null || echo "")
    local query
    query=$(echo "$url" | python3 -c "
import sys, re
from urllib.parse import urlparse
u = sys.stdin.read().strip()
path = urlparse(u).path
# Extract meaningful words from URL path
slug = re.sub(r'[-_]', ' ', path.strip('/'))
print(slug[:80])
" 2>/dev/null || echo "$url")
    
    echo "🔍 搜尋 HN: $query"
    hn-search "$query" 3
    
    # 2. 也試 Google News (如果需要的話)
    echo ""
    echo "💡 若 HN 沒有討論，可手動搜尋 Google News："
    echo "   https://news.google.com/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$query'''))")"
}

# 如果直接執行（非 source），顯示可用指令
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "llm-news-crawler 工具集"
    echo "======================="
    echo ""
    echo "用法: source scripts/checks.sh   # 載入全部函式"
    echo "      bash scripts/checks.sh     # 顯示這份說明"
    echo ""
    echo "可用函式："
    echo "  fetch-blog URL          用 Playwright 抓部落格"
    echo "  fetch-smart URL         用 smart fetch 抓（HN/arXiv）"
    echo "  hn-search 'query'       搜 HN 討論串"
    echo "  hn-fetch STORY_ID       抓 HN 討論內容"
    echo "  arxiv-fetch ARXIV_ID    抓 arXiv 論文摘要"
    echo "  cron-today [COLLECTOR]  看今天爬到的全部新聞"
    echo "  cron-digest [DIGEST]    看中午匯總"
    echo "  check-cf URL            測 URL 是否被 Cloudflare 擋"
    echo "  test-playwright URL     直接用 Playwright 測抓取"
    echo "  cf-fallback URL         原站被擋時：自動搜 HN + Google News 替代來源"
    echo ""
    echo "常用組合："
    echo "  # 查 HN 討論 → 抓內容"
    echo "  hn-search 'Claude Opus' && hn-fetch 48311647"
    echo ""
    echo "  # 測來源可用性 → 決定用哪種方式抓"
    echo "  check-cf https://openai.com/... || test-playwright https://openai.com/..."
    echo ""
    echo "  # 被 Cloudflare 擋 → 自動找替代來源"
    echo "  cf-fallback https://www.anthropic.com/news/claude-opus-4-8"
fi

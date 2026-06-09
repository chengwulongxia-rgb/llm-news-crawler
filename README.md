# LLM News Crawler

龍蝦城武 LLM 新聞爬蟲 — 多來源自動蒐集 LLM/AI 新聞 + 單篇文章擷取。

## 架構

```
src/crawler/
├── models.py              # Pydantic 資料模型 (NewsItem)
├── filters.py             # LLM 關鍵詞過濾器 (50+ 關鍵詞)
├── dedup.py               # 跨輪次 URL 去重 (48h TTL)
├── article_fetcher.py     # 單篇文章擷取 (httpx + Playwright)
├── hard_fetch.py           # 硬爬模組：arXiv 摘要、HN 討論串、智慧路由
├── sources/
│   ├── hackernews.py      # HN Firebase API
│   ├── rss_feeds.py       # RSS/Atom feeds (OpenAI, Google, ArXiv×2)
│   ├── sitemap_blogs.py   # Sitemap 解析 (Anthropic, Mistral)
│   └── playwright_blogs.py # Playwright JS 渲染 (Anthropic, Mistral)
└── main.py                # CLI 入口 (llm-crawler)
```

## 安裝

```bash
# 安裝依賴
uv sync

# 安裝 Playwright 瀏覽器（必要！文章擷取和 JS 渲染來源需要）
uv run playwright install chromium
```

> **⚠️ 重要：** `--fetch` 單篇文章擷取 和 Anthropic/Mistral 部落格來源都依賴 Playwright。
> 如果跳過 `playwright install chromium`，這些功能會自動降級（無摘要）或失敗。

## 使用

### 新聞蒐集

```bash
uv run llm-crawler                          # 全來源蒐集，文字輸出
uv run llm-crawler --json                   # JSON 輸出
uv run llm-crawler --min-score 10           # 只留 >= 10 分的
uv run llm-crawler --limit 5                # 最多 5 則
uv run llm-crawler -o /tmp/news.txt         # 輸出到檔案
uv run llm-crawler --sources hackernews     # 只跑特定來源
uv run llm-crawler --no-dedup               # 停用跨輪次去重
```

### 單篇文章擷取 (NEW!)

```bash
# 擷取文章內文 → 輸出 Markdown
uv run llm-crawler --fetch "https://文章網址"

# 強制使用 Playwright（JS 渲染網站）
uv run llm-crawler --fetch "https://文章網址" --playwright

# 輸出 JSON 格式
uv run llm-crawler --fetch "https://文章網址" --playwright --json

# 智慧擷取（自動辨識網站類型，arXiv/HN/一般網站用最佳方式）
uv run llm-crawler --fetch-smart "https://arxiv.org/abs/2606.06635"
uv run llm-crawler --fetch-smart "https://news.ycombinator.com/item?id=48439240"

# 批次擷取（從檔案讀 URL 清單，平行擷取）
uv run llm-crawler --fetch-batch urls.txt

# 存到檔案
uv run llm-crawler --fetch "https://文章網址" --playwright -o /tmp/article.md
```

擷取順序：先試 httpx（快），失敗自動降級 Playwright（慢但支援 JS 渲染）。
`--playwright` 強制跳過 httpx 直接使用瀏覽器。

### 去重管理

```bash
uv run llm-crawler --dedup-stats     # 查看去重記錄
uv run llm-crawler --clear-dedup     # 清除記錄
uv run llm-crawler --no-dedup        # 本次不停用去重
```

## 資料來源

| 來源 | 方式 | 類型 |
|------|------|------|
| HackerNews | Firebase API | 社群熱議 |
| OpenAI Blog | RSS | 官方公告 |
| Google AI Blog | RSS | Gemini/產品更新 |
| Google Research | Atom | 研究論文 |
| ArXiv cs.CL | RSS | NLP 論文（平日） |
| ArXiv cs.AI | RSS | AI 論文（平日） |
| Anthropic Blog | Playwright 🎭 | Claude 開發（含摘要） |
| Mistral News | Playwright 🎭 | 開源模型動態（含摘要） |

## 管線整合

這個爬蟲被整合到 Hermes cron 自動化管線中：

```
每 2h: llm-news-crawler.sh → 蒐集記錄（備用監控）
每天 00:00: 發布器 → uv run llm-crawler → 翻譯+點評 → git push
```

## Playwright 說明

Playwright 用於兩類場景：

1. **JS 渲染部落格**（Anthropic、Mistral）：這些網站是 React/Next.js，靜態 HTTP 拿不到內文，需要 headless Chromium 渲染後提取
2. **單篇文章擷取**（`--fetch`）：部分部落格（如 bearblog.dev）有反 bot 機制，需要模擬真實瀏覽器

### 反 bot 偽裝

Playwright 啟動時會自動：
- 隱藏 `navigator.webdriver` 屬性
- 使用標準 Chrome user-agent
- 禁用 AutomationControlled 特徵標記

### 已知限制

- `playwright install chromium` 需要下載 ~150MB 瀏覽器
- 非 Ubuntu 24.04 系統會使用 fallback build
- 部分網站（Cloudflare 防護）仍可能阻擋

## 關鍵詞過濾

見 `filters.py` — 涵蓋模型名、技術名、平台工具、學術會議、硬體等維度。

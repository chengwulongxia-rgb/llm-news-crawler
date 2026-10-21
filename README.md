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
│   ├── rss_feeds.py       # RSS/Atom feeds (官方、arXiv、獨立作者)
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
uv run llm-crawler --sources ai-security --min-score 10 --max-age-days 7
bash scripts/ai-security-watch.sh               # 手動跑監測同款：近 3 天＋跨批去重
# AI 資安事故候選：英文／西文 Google News，排除明顯教學文、無日期與無效網址；仍需擷取全文確認事件
uv run llm-crawler --no-dedup               # 停用跨輪次去重
```

### 深挖候選：正文證據與待驗證案例

新聞流保持原來的熱度門檻、文字條目與前 N 則輸出；另外產生一份**獨立的選題資料**，不把所有新聞硬寫成長文：

```bash
uv run llm-crawler --min-score 10 --max-age-days 3 --limit 20 --no-dedup \
  --deep-dive-output data/deep-dive/manual.json --deep-dive-budget 12
```

- 新聞 stdout 不混入候選卡，`--json` 仍是新聞陣列；新聞 JSON 新增可選資料欄位 `discussion_url`，保留 HN 原討論串與回覆數。
- 先完成新聞 stdout／`--output` 寫出，再產生候選。候選評估的 `ValueError` 或快照／JSON／Markdown 寫入的 `OSError` 會在 stderr 明確警告（含輸出路徑、錯誤類型與原因），不吞新聞；一般新聞流成功時 CLI 仍 exit 0，不代表候選成功。警告時候選檔可能僅部分更新或保留舊版，不能當作本批完整報告；中斷與其他非預期錯誤不會被此隔離隱藏。
- 候選池在新聞標題／熱度門檻、跨批次新聞去重及 `--limit` **之前**分流，但仍套用同一個日期上限；無日期者會明標缺口，不能當近期文章。
- AI 主題 RSS 可找回標題沒有模型名稱的文章；其他候選以標題＋摘要相關性選入。這不是全網搜尋或語意理解器，沒被既有來源抓到的文章仍不會自動出現。
- 正文預算預設 12、可設 1–40，每次最多 3 個並行；依發布者角色輪流分配，優先獨立作者、社群，再看研究與公司。角色內先看技術線索，後看新近程度，**不使用 HN 點數排名**。預篩仍是啟發式，並不保證不漏好文章。
- 僅此正文選題階段使用 HTTP-only 擷取：請求指定 `Accept-Encoding: identity`，任何非 identity 的 `Content-Encoding` 在讀取正文前記為 `unsupported_encoding`，不進行自動解壓；每篇最長 20 秒、最多 1 MB 回應、最多保留 75,000 字元，超限、擷取失敗、疑似牆頁都記錄原因，不拿 RSS 摘要代替正文。JS-only 頁面、PDF 全文與材料連結不會自動追抓，也不偷偷啟動瀏覽器或 LLM。
- 每次輸出指定的 `.json`、同名 `.md` 卡片，以及同目錄 `snapshots/<sha256>.txt` 正文快照。快照是 HTTP 擷取並清理後的文字，**不是保證完整的原始網頁／PDF**；有 `fetched_url`、`body_sha256`、`body_truncated` 可核對摘錄。檔案採單檔原子更新；整批 JSON／Markdown 不是同一個交易，消費端以 JSON 為準。
- `stats` 區分 pool（日期篩選後原始筆數，含重複）、eligible（相關且 URL 去重後）、fetched（有嘗試的頁數）、ok、failed、proposals（待驗證提案）、not_fetched（未用正文預算擷取）。不能把沒有擷取完說成沒有候選。

卡片內容包含多選內容類型、發布者角色、正文摘錄及來源 URL、材料連結、問題與缺口。有重現步驟、結果線索、GitHub 材料連結時，才產生案例／條件／驗收**提案**。目前材料辨識以 GitHub repo／檔案連結為主，規格、資料集及非 GitHub 實作仍需人工補查。彙整文章不把不同子新聞的步驟與結果拼成一個案例。沒有這些線索的新聞、政策或研究文仍保留，不因沒有程式碼就判無價值。

**這是 `rules-v1` 關鍵詞線索，不是事實查核、語意分類模型或實作可行性證明。** 例如普通文章中的 stack、attention、benchmark 仍可能誤中；分數只作可追溯的選題提示。來源角色辨識是有限域名表，HN 發現的公司文章仍標 vendor；未知域名不假稱獨立作者。原文中的命令、結果與 GitHub 連結都未執行／逐一驗證，成本與 CPU/GPU/API 條件一律待確認；`experiment_status` 固定 `not_run`，不能稱已重現。

安全邊界：自動擷取前與每個重新導向都做公開 URL／DNS 檢查，不執行 JS 或來源程式、不追材料連結、不服從網頁裡的 agent 指令；**DNS rebinding 尚非硬隔離防護**，不要把爬蟲部署在能任意存取敏感內網的環境。正文及報告保存在被 Git 忽略的 `/data/`，不隨 repo 發布；目前不自動清理歷史快照，部署者須自行管理容量。

`bash scripts/llm-news-crawler.sh` 現在每批另存 `data/deep-dive/YYYY-MM-DD_HHMMSS.{json,md}`（台北時間），保留原新聞輸出。中午匯總提示詞的版本來源在 `cron/noon-digest-prompt.md`：讀近兩日候選，先放值得深挖的題目，再保留一般新聞，仍用同一套編號供選擇。排程時間、日報自動發布器與資安事故獨立來源不變；選完才完成查證／實作／寫作，再併入當天日報。

測試：`uv run --with pytest pytest tests/ -q`。

### 中午通知品質檢查（程式掌握結構）

```bash
uv run llm-digest --job-id e0349bb0f3b1
# 同款 cron 入口：
bash scripts/noon-digest.sh
```

這個指令使用**既有本機收集資料**，不重新爬網。近兩個台北曆日的新聞與候選先合併為 manifest，以原 URL 精確去重；同一候選先挑成功且有證據的版本，再挑較新評估，不以更多關鍵詞誤判蓋過新版修正。可判定的數字／英文月份／日期 slug 舊文會排除並留下警示及 excluded 紀錄；不能判定的日期不擅自補齊，近兩日只保證收集批次範圍。

只有正體中文單行摘要交給模型。`scripts/digest_summarize.py` 讀取既有工作的 model/provider，使用 Hermes SDK、空工具清單、不載入專案規則／記憶／人格；目前需要工作已有明確 model/provider，缺少即失敗，不自作主張改模型。摘要只能回每個固定 id 的映射；時間、網址、編號、總數、分類與案例狀態一律由程式產生。

`digest.py` 的硬檢查涵蓋：當次實際台北時間、宣告總數、連續編號、URL 順序／唯一性、摘要不得注入新 URL 或編號。一般新聞保持簡潔，候選才顯示分類或實作所需的材料缺口；英文摘錄與例行「未查證」備註留在 manifest，不逐篇刷通知。已處理的舊文只彙總排除數，未解決的資料異常仍明示；所有實驗在紀錄內維持 `not_run`，通知以「案例尚未實作」說明。結果、manifest、模型摘要映射、validation 分別存在忽略 Git 的 `data/digests/<時間>/`。寫檔或驗證失敗會 stderr 明報、非零退出，**不輸出可送出的通知清單**。

中午 cron 保留原時間、delivery 與模型欄位，改成 `no_agent=true` 執行已驗證的程式 stdout，不再讓外層代理自由重寫。模型仍在程式內沿用既有設定做摘要，不是取消摘要模型。`cron/noon-digest-prompt.md` 現為執行／審計說明。

Hermes SDK 使用安裝的 `hermes --print-runtime-command` 機器介面取得 bootstrap，再以固定 `run_path` 入口執行摘要 worker；不固定可能被依賴 GC 清掉的 Python 版本路徑。未知 launcher 協定直接失敗，不嘗試執行任意來源指令。這個整合需要目前支援該介面的 Hermes source 安裝。

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
| Simon Willison | Atom | 獨立開發者實作與評論 |
| Interconnects | RSS | 獨立產業／研究分析 |
| Latent Space | RSS | 工程訪談與 AI News 彙整（注意與原始新聞去重） |
| Sebastian Raschka | RSS | 模型與研究技術拆解 |
| ArXiv cs.CL | RSS | NLP 論文（平日） |
| ArXiv cs.AI | RSS | AI 論文（平日） |
| Anthropic Blog | Playwright 🎭 | Claude 開發（含摘要） |
| Mistral News | Playwright 🎭 | 開源模型動態（含摘要） |

每批爬取會在**文字輸出末尾**記錄 `📊 入選來源（N 則）：來源 數量…`；新聞條目仍以 `•` 開頭，`--json` 維持純 JSON。觀察接下來一週的 cron 輸出，統計新來源實際**入選**數量；它不代表所有已抓取文章（舊聞、標題未中關鍵字、超過 20 則上限的文章不計）。`--no-dedup` 可用於不影響去重紀錄的手動抽查。

## 管線整合

這個爬蟲被整合到 Hermes cron 自動化管線中：

```
每天 09:00、21:00: llm-news-crawler.sh → 新聞記錄 + 獨立深挖候選（無 LLM）
每天 11:45: 日報發布器 → 原新聞流 → 查證、寫作、git push
每 2 天 12:05: 中午匯總 → 新聞 + 近兩日候選卡 → 供使用者選題
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

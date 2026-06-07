# LLM News Crawler

龍蝦城武 LLM 新聞爬蟲 — 從 Hacker News API 自動蒐集 LLM/AI 相關新聞。

## 架構

```
src/crawler/
├── models.py          # Pydantic 資料模型 (NewsItem)
├── filters.py         # LLM 關鍵詞過濾器
├── sources/
│   └── hackernews.py  # HN Firebase API 爬蟲
└── main.py            # CLI 入口 (llm-crawler)
```

## 使用

```bash
# 安裝
uv sync

# 執行
uv run llm-crawler                      # 文字格式輸出到 stdout
uv run llm-crawler --json               # JSON 輸出
uv run llm-crawler --min-score 10       # 只留 >= 10 分的
uv run llm-crawler --limit 5            # 最多 5 則
uv run llm-crawler -o /tmp/news.txt     # 輸出到檔案
```

## 管線整合

這個爬蟲被整合到 Hermes cron 自動化管線中：

```
每 2h: llm-news-crawler.sh → 蒐集記錄（備用監控）
每天 00:00: 發布器 → uv run llm-crawler → 翻譯+點評 → git push
```

## 資料來源

- **Hacker News** (Firebase API): top stories + new stories，篩選 LLM 相關關鍵詞
- 預計擴充: Reddit r/MachineLearning、ArXiv

## 關鍵詞過濾

見 `filters.py` — 涵蓋模型名、技術名、平台工具、學術會議、硬體等維度。

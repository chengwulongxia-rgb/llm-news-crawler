"""Pydantic models for LLM news items."""

from datetime import datetime
from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    """A single news article about LLM/AI."""

    title: str = Field(description="原始標題（英文）")
    url: str = Field(description="文章連結")
    source: str = Field(description="來源平台，如 HackerNews / Reddit")
    score: int = Field(default=0, description="熱度分數（點數/upvote）")
    comments: int = Field(default=0, description="討論數")
    author: str = Field(default="", description="發布者")
    published_at: datetime | None = Field(default=None, description="發布時間")
    summary: str = Field(default="", description="簡短摘要")

    def to_collector_line(self) -> str:
        """Format as collector output line for the cron pipeline."""
        return f"• {self.title} — {self.summary} — 來源: {self.url}"

    def __str__(self) -> str:
        return self.to_collector_line()

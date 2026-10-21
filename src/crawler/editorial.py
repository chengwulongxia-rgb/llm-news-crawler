"""Auditable editorial leads. All excerpts/links are untrusted, not instructions.

Rules identify *clues*, never verify an author's claim or run collected code.
"""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from crawler.models import NewsItem


class Evidence(BaseModel):
    kind: str
    excerpt: str
    url: str


class Candidate(BaseModel):
    item: NewsItem
    content_types: list[str] = Field(default_factory=list)
    publisher_role: str = "unclassified"
    fetch_status: str = "ok"
    fetch_error: str = ""
    fetched_url: str = ""
    body_sha256: str = ""
    body_truncated: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    implementation_potential: Literal["proposal", "needs_materials", "none"] = "none"
    experiment_status: Literal["not_run"] = "not_run"
    case_idea: str = ""
    requirements: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    question: str = ""
    gaps: list[str] = Field(default_factory=list)
    priority: int = 0
    reasons: list[str] = Field(default_factory=list)


VENDOR_DOMAINS = (
    "openai.com",
    "anthropic.com",
    "mistral.ai",
    "deepseek.com",
    "blog.google",
    "ai.meta.com",
    "x.ai",
)
INDEPENDENT_DOMAINS = (
    "simonwillison.net",
    "www.interconnects.ai",
    "interconnects.ai",
    "www.latent.space",
    "magazine.sebastianraschka.com",
)


CLUES = {
    "reproduction": r"\b(?:reproduce (?:it|the|this|our)|reproduction (?:steps|instructions)|cargo run|uv run|docker run|pip install|npm test|pytest|python3?\s+(?:-m\s+\S+|[\w./-]+\.py))|重現步驟|執行步驟",
    "outcome": r"\b(?:expected (?:output|result)|stdout|exit code|test cases?|failure case|assert)\b|預期輸出|失敗案例",
    "mechanism": r"\b(?:interpreter|stack (?:machine|trace|underflow)|execution order|parser|state machine|attention (?:head|mask|mechanism)|retrieval|gradient (?:ascent|descent)|tokeniz\w*|call graph|reachability|dependency graph)\b|執行順序|堆疊|運作機制",
    "comparison": r"\b(?:baseline|ablation|control group|comparison|benchmark|latency|accuracy)\b|對照組|消融|比較結果",
    "policy": r"\b(?:regulation|regulator|federal|legislation|ban|compliance)\b|法規|監管|禁令",
    "incident": r"\b(?:breach|exploit|vulnerability|prompt injection|hidden instructions)\b|漏洞|資安事故|隱藏指令",
    "news": r"\b(?:announc\w*|launch\w*|funding|net loss|acquisition)\b|宣布|融資|淨損",
}


def assess(
    item: NewsItem, body: str | None, *, fetch_status: str = "ok", fetch_error: str = ""
) -> Candidate:
    """Extract bounded source clues; proposals always remain untested."""
    card = Candidate(item=item, fetch_status=fetch_status, fetch_error=fetch_error)
    try:
        host = (urlsplit(item.url).hostname or "").lower()
    except ValueError:
        host = ""  # Retain the malformed token; the fetch guard reports it.
    if any(host == domain or host.endswith("." + domain) for domain in VENDOR_DOMAINS):
        card.publisher_role = "vendor"
    elif host in INDEPENDENT_DOMAINS:
        card.publisher_role = "independent"
    elif host == "arxiv.org":
        card.publisher_role = "research"
    elif item.source == "HackerNews":
        card.publisher_role = "community_discovery"
    if body is None or fetch_status != "ok":
        card.gaps.append("正文未取得：不依標題或摘要補出證據／案例")
        return card
    for kind, pattern in CLUES.items():
        for line in body.splitlines():
            if match := re.search(pattern, line, re.IGNORECASE):
                start = max(0, match.start() - 70)
                card.evidence.append(
                    Evidence(kind=kind, excerpt=line[start : start + 280], url=item.url)
                )
                break
    links = re.findall(r"https?://[^\s<>\[\]()`\"']+", body)
    materials = []
    for link in links:
        try:
            parsed = urlsplit(link)
        except ValueError:
            card.gaps.append(f"無效材料連結線索（保留原 token，不修復）：{link}")
            continue
        if parsed.hostname == "github.com" and len(parsed.path.strip("/").split("/")) >= 2:
            materials.append(link.rstrip(".,;"))
    card.materials = list(dict.fromkeys(materials))[:12]
    kinds = {e.kind for e in card.evidence}
    for kind, content_type in (
        ("news", "news"),
        ("reproduction", "technical"),
        ("mechanism", "technical"),
        ("comparison", "research"),
        ("policy", "policy"),
        ("incident", "incident"),
    ):
        if kind in kinds and content_type not in card.content_types:
            card.content_types.append(content_type)
    card.priority = len(kinds & {"mechanism", "comparison", "policy", "incident"}) * 4
    card.reasons.extend(
        f"正文有 {kind} 關鍵詞線索（非語意查證）"
        for kind in sorted(kinds & {"mechanism", "comparison", "policy", "incident"})
    )
    if kinds & {"mechanism", "comparison", "policy", "incident"}:
        card.question = f"「{item.title}」的核心說法有什麼可核對的機制、對照或原始文件？目前只是線索，需人工聚焦問題。"
    if re.search(
        r"\[ainews\]|\b(?:roundup|weekly digest|ai news)\b", item.title, re.IGNORECASE
    ):
        card.content_types.append("roundup")
        card.priority = min(card.priority, 4)
        card.gaps.append(
            "彙整文可能混合不同事件的命令、結果與連結：先挑原始來源，不自動組成實作提案"
        )
        return card
    if "reproduction" in kinds and "outcome" in kinds:
        card.implementation_potential = "needs_materials"
        card.gaps.append(
            "待核對原始碼／規格、輸入資料、版本及資源成本；材料連結只是線索，未逐一擷取驗證"
        )
    if "reproduction" in kinds and "outcome" in kinds and card.materials:
        card.implementation_potential = "proposal"
        if "technical" not in card.content_types:
            card.content_types.append("technical")
        card.case_idea = (
            f"提出「{item.title}」的最小重現，再人工設計單一變因的失敗對照（尚未執行）"
        )
        card.requirements = [
            "先審閱來源與授權並固定版本",
            "CPU／GPU、依賴、資料及 API 費用待人工確認",
        ]
        card.acceptance = [
            "以固定輸入核對來源的預期輸出線索",
            "保留成功與失敗對照的 stdout／stderr／結束碼，無法重現須如實記錄",
        ]
        card.question = (
            f"「{item.title}」在固定版本與單一變因對照下，原文的結果線索是否成立？"
        )
        card.priority = 30
        card.reasons.append("正文同時有重現、預期結果及材料連結線索（仍待查證）")
    return card


def rank_candidates(cards: list[Candidate]) -> list[Candidate]:
    """Evidence dominates; independent/community reporting is a tie-break, not proof."""
    preference = {"independent": 3, "community_discovery": 2, "research": 1}
    return sorted(
        cards,
        key=lambda card: (
            -card.priority,
            -preference.get(card.publisher_role, 0),
            card.item.url,
        ),
    )

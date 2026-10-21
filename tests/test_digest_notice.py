"""Reader-facing notices aggregate handled outcomes, never hide data errors."""

import copy

from crawler.digest import render_digest, validate_digest


def manifest():
    return {
        "generated_at": "2026-10-21T16:37:12+08:00",
        "days": 2,
        "entries": [
            {
                "id": "1",
                "title": "Article",
                "url": "https://example.org/news",
                "candidate": {
                    "fetch_status": "ok",
                    "content_types": ["technical"],
                    "evidence": [{"excerpt": "awkward English sentence fragment"}],
                    "implementation_potential": "none",
                    "gaps": ["來源線索未逐一查證"],
                },
            }
        ],
        "warnings": [],
        "excluded": [],
    }


def test_handled_old_articles_become_one_line_without_losing_audit_records():
    data = manifest()
    for i in range(8):
        record = {
            "url": f"https://old.example/{i}",
            "reason": "發表日期超出範圍",
            "kind": "old_article",
        }
        data["excluded"].append(record)
        data["warnings"].append(f"{record['url']}：{record['reason']}")
    original = copy.deepcopy(data)
    text = render_digest(data, {"1": "來源介紹一項工具。"})
    assert "已排除 8 篇舊文" in text and text.count("已排除") == 1
    assert "警示：" not in text and "連結省略" not in text
    assert data == original
    validate_digest(text, data)


def test_model_triage_notes_stay_out_of_selection_notice():
    data = manifest()
    text = render_digest(data, {"1": "來源介紹一項工具。"})
    assert "awkward English" not in text and "來源線索未逐一查證" not in text
    assert "來源線索／缺口" not in text and "not_run" not in text
    assert text.count("案例尚未實作") == 1


def test_real_data_errors_remain_visible_even_when_old_articles_were_handled():
    data = manifest()
    data["warnings"] = ["2026-10-21_bad.json: 無法解析候選 JSON：syntax error"]
    text = render_digest(data, {"1": "來源介紹一項工具。"})
    assert "資料異常：" in text and "syntax error" in text
    validate_digest(text, data)


def test_actionable_proposal_gaps_are_not_hidden():
    data = manifest()
    data["entries"][0]["candidate"].update(
        implementation_potential="proposal",
        case_idea="最小對照實驗",
        gaps=["授權未核對"],
    )
    text = render_digest(data, {"1": "來源介紹一項工具。"})
    assert "授權未核對" in text and "最小對照實驗" in text and "待驗證" in text
    validate_digest(text, data)

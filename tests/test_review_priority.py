import copy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from craneassist.api import create_app
from craneassist.config import Settings
from craneassist.engine import DemoProvider, execute, new_run
from craneassist.models import ReviewRequest
from craneassist.review import review_projection
from craneassist.store import Store


def test_priority_uses_structural_warnings_and_never_invents_confidence():
    run = {
        "candidates": [
            {
                "code": "A1",
                "evidence_relation": "not_applicable",
                "support_type": "insufficient_material",
            },
            {"code": "B3", "evidence_relation": "indirect", "support_type": "supports"},
            {"code": "D4", "evidence_relation": "direct", "support_type": "supports"},
        ],
        "reviews": {},
        "audit": {
            "warnings": [
                "B3:priority_human_boundary_review",
                "B3/D4:shared_passage_check_distinct_facts",
            ]
        },
    }
    before = copy.deepcopy(run)
    projection = review_projection(run)
    assert [row["code"] for row in projection["review_queue"]] == ["B3", "D4", "A1"]
    assert projection["review_summary"]["priority_pending"] == 2
    assert "confidence" not in str(projection)
    assert run == before


def test_reviewed_items_follow_pending_and_reason_counts_use_latest_only():
    run = {
        "candidates": [{"code": "B3", "evidence_relation": "indirect"}, {"code": "A1"}],
        "reviews": {"B3": {"reason_codes": ["evidence_insufficient"]}},
        "review_history": [{"code": "B3", "reason_codes": ["fact_missing"]}],
    }
    projection = review_projection(run)
    assert projection["review_queue"][0]["code"] == "A1"
    assert projection["review_summary"]["pending"] == 1
    counts = projection["review_summary"]["reason_counts"]
    assert counts["evidence_insufficient"] == 1 and counts["fact_missing"] == 0


@pytest.mark.parametrize("reasons", [["unknown_reason"], ["fact_missing", "fact_missing"]])
def test_invalid_or_duplicate_reasons_are_rejected(reasons):
    with pytest.raises(ValidationError):
        ReviewRequest(
            revision=0,
            label_value="unknown",
            reviewer="synthetic-test",
            comment="这是合成接口验证意见。",
            evidence=[],
            reason_codes=reasons,
        )


def test_old_reviews_without_reason_remain_readable():
    projection = review_projection({"reviews": {"A1": {"comment": "legacy"}}})
    assert projection["review_summary"]["reviews_without_reason"] == 1
    assert not any(projection["review_summary"]["reason_counts"].values())


def test_reason_history_export_and_source_immutability(tmp_path):
    store = Store(tmp_path)
    case = store.add_case(
        {
            "case_id": "DEMO",
            "title": "合成复核接口测试",
            "source_type": "SYNTHETIC",
            "paragraphs": [
                {"span_id": "DEMO-P0001", "text": "这是一份合成教学测试资料。"},
                {
                    "span_id": "DEMO-P0002",
                    "text": "现场负责人已发现连接销轴未安装，仍安排班组继续顶升，未纠正该问题。",
                },
            ],
        }
    )
    settings = Settings(tmp_path)
    run = new_run(case, "demo")
    store.save_run(run)
    run = execute(store, settings, run["run_id"], DemoProvider(store.codebook))
    original_candidates = copy.deepcopy(run["candidates"])
    case_hash = case["content_sha256"]
    with TestClient(create_app(settings)) as client:
        detail = client.get(f"/api/runs/{run['run_id']}").json()
        assert detail["review_summary"]["pending"] == 16
        assert detail["review_queue"][0]["code"] == "B3"
        payload = {
            "revision": 0,
            "label_value": "unknown",
            "reviewer": "synthetic-test",
            "comment": "合成接口检查：仅测试复核保存与历史。",
            "evidence": [],
            "decision": "modify",
            "reason_codes": ["evidence_insufficient"],
        }
        path = f"/api/runs/{run['run_id']}/reviews/B3"
        first = client.put(path, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["review_summary"]["reason_counts"]["evidence_insufficient"] == 1
        payload.update(revision=1, reason_codes=["unsupported_inference"])
        second = client.put(path, json=payload).json()
        assert second["review_history"][-1]["reason_codes"] == ["evidence_insufficient"]
        assert second["candidates"] == original_candidates
        exported = client.get(f"/api/runs/{run['run_id']}/export").json()
        assert exported["review_summary"]["reason_counts"]["unsupported_inference"] == 1
        markdown = client.get(f"/api/runs/{run['run_id']}/export?format=md").text
        assert "推理超出原文" in markdown
    assert store.get_case("DEMO")["content_sha256"] == case_hash

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from craneassist.api import create_app
from craneassist.config import Settings
from craneassist.engine import DemoProvider, ToolSession, execute, new_run
from craneassist.store import Store, digest, write_json


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path)
    result.add_case(
        {
            "case_id": "DEMO",
            "title": "合成例",
            "source_type": "SYNTHETIC",
            "provenance": {"synthetic": True},
            "paragraphs": [
                {"span_id": "DEMO-P0001", "text": "以下为自行编写的合成演示材料，不对应真实事故。"},
                {
                    "span_id": "DEMO-P0002",
                    "text": "现场负责人已发现连接销轴未安装，仍安排班组继续顶升，未纠正该问题。",
                },
                {"span_id": "DEMO-P0003", "text": "事故防范和整改措施建议"},
                {"span_id": "DEMO-P0004", "text": "建议相关单位加强培训、完善管理制度。"},
            ],
        }
    )
    return result


def submission(store):
    provider = DemoProvider(store.codebook)
    provider.complete([], [])
    call = provider.complete([], [])["message"]["tool_calls"][0]
    return json.loads(call["function"]["arguments"])


def session(store):
    result = ToolSession(store.get_case("DEMO"), store.codebook)
    result.call("get_codebook", {})
    result.call("read_passages", {"offset": 0, "limit": 30})
    return result


def completed_run(store):
    run = new_run(store.get_case("DEMO"), "demo")
    store.save_run(run)
    result = execute(store, Settings(store.root), run["run_id"])
    assert result["status"] == "ready_for_review"
    return result


def test_exact_quote_and_complete_reading_required(store):
    tool = ToolSession(store.get_case("DEMO"), store.codebook)
    result = tool.call("submit_candidates", submission(store))
    assert not result["ok"]
    assert "read_codebook_before_submission" in result["errors"]
    assert any("unread_passages" in error for error in result["errors"])


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("fabricated_quote", "quote_not_exact"),
        ("foreign_span", "unknown_span"),
        ("recommendation", "recommendation_cannot_prove_preaccident_fact"),
        ("duplicate_code", "must_cover_each"),
        ("positive_without_evidence", "positive_requires"),
        ("unknown_supports", "unknown_cannot"),
    ],
)
def test_rejects_material_evidence_failures(store, mutation, expected):
    payload = submission(store)
    b3 = next(row for row in payload["candidates"] if row["code"] == "B3")
    if mutation == "fabricated_quote":
        b3["evidence"][0]["quote"] = "原文没有出现的故意捏造事实。"
    elif mutation == "foreign_span":
        b3["evidence"][0]["span_id"] = "TC999-P0002"
    elif mutation == "recommendation":
        b3["evidence"] = [
            {"span_id": "DEMO-P0004", "quote": "建议相关单位加强培训、完善管理制度。"}
        ]
    elif mutation == "duplicate_code":
        payload["candidates"][0]["code"] = "B3"
    elif mutation == "positive_without_evidence":
        b3["evidence"] = []
    elif mutation == "unknown_supports":
        b3["label_value"] = "unknown"
    result = session(store).call("submit_candidates", payload)
    assert not result["ok"]
    assert any(expected in str(error) for error in result["errors"])


def test_search_does_not_count_as_complete_reading(store):
    tool = ToolSession(store.get_case("DEMO"), store.codebook)
    tool.call("search_passages", {"query": "负责人"})
    assert tool.read_ids == set()


def test_later_invalid_submission_clears_earlier_acceptance(store):
    tool = session(store)
    payload = submission(store)
    assert tool.call("submit_candidates", payload)["ok"]
    payload["candidates"][0]["code"] = "B3"
    assert not tool.call("submit_candidates", payload)["ok"]
    assert tool.accepted is None


def test_agent_receives_validation_feedback_and_corrects_quote(store):
    class CorrectingProvider:
        model = "feedback-test-double-not-a-model"

        def __init__(self):
            self.turn = 0

        def complete(self, messages, tools):
            self.turn += 1
            if self.turn == 1:
                calls = [("get_codebook", {}), ("read_passages", {"offset": 0, "limit": 30})]
            else:
                payload = submission(store)
                if self.turn == 2:
                    b3 = next(r for r in payload["candidates"] if r["code"] == "B3")
                    b3["evidence"][0]["quote"] = "不存在于原文中的捏造事实。"
                else:
                    assert "quote_not_exact" in messages[-1]["content"]
                calls = [("submit_candidates", payload)]
            return {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{self.turn}_{i}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args, ensure_ascii=False),
                            },
                        }
                        for i, (name, args) in enumerate(calls)
                    ],
                },
                "usage": {},
            }

        def close(self):
            pass

    run = new_run(store.get_case("DEMO"), "deepseek")
    store.save_run(run)
    result = execute(store, Settings(store.root), run["run_id"], CorrectingProvider())
    assert result["status"] == "ready_for_review"
    events = [e for e in result["events"] if e.get("name") == "submit_candidates"]
    assert [e["result"]["ok"] for e in events] == [False, True]


def test_tool_arguments_reject_extra_fields_and_wrong_types(store):
    tool = session(store)
    assert not tool.call("read_passages", {"offset": 0, "limit": 30, "path": "../secret"})["ok"]
    assert not tool.call("read_passages", {"offset": "0", "limit": 30})["ok"]
    assert not tool.call("shell", {"command": "anything"})["ok"]


def test_same_act_not_both_d1_d4(store):
    payload = submission(store)
    b3 = next(r for r in payload["candidates"] if r["code"] == "B3")
    for row in payload["candidates"]:
        if row["code"] in {"D1", "D4"}:
            code = row["code"]
            row.update(copy.deepcopy(b3))
            row["code"] = code
    result = session(store).call("submit_candidates", payload)
    assert not result["ok"]
    assert "D1/D4:same_act_cannot_be_confirmed_as_both" in result["errors"]


def test_hash_tampering_and_duplicate_content_are_detected(store):
    original = store.get_case("DEMO")
    duplicate = copy.deepcopy(original)
    duplicate["case_id"] = "COPY"
    with pytest.raises(ValueError, match="duplicate_report_content"):
        store.add_case(duplicate)
    original["paragraphs"][0]["text"] = "篡改过的原文"
    write_json(store.case_dir / "DEMO.json", original)
    with pytest.raises(ValueError, match="hash_mismatch"):
        store.get_case("DEMO")


def test_full_workflow_and_exports_preserve_separate_human_decisions(store):
    original_hash = digest(store.get_case("DEMO"))
    run = completed_run(store)
    with TestClient(create_app(Settings(store.root))) as client:
        review = {
            "revision": 0,
            "label_value": "unknown",
            "reviewer": "工程核验",
            "comment": "这是合成测试的复核记录，不是实际人工标注。",
            "evidence": [],
            "exclusion_coverage": "",
        }
        response = client.put(f"/api/runs/{run['run_id']}/reviews/B3", json=review)
        assert response.status_code == 200
        saved = response.json()
        assert next(x for x in saved["candidates"] if x["code"] == "B3")["label_value"] == "1"
        assert saved["reviews"]["B3"]["label_value"] == "unknown"
        assert not saved["expert_validated"]
        assert not saved["training_or_evaluation_admitted"]
        assert not saved["human_independent_annotation_completed"]
        stale = client.put(f"/api/runs/{run['run_id']}/reviews/B3", json=review)
        assert stale.status_code == 400
        assert "conflict" in stale.json()["detail"]
        exported = client.get(f"/api/runs/{run['run_id']}/export").json()
        assert len(exported["run"]["candidates"]) == 16
        assert exported["case"]["content_sha256"] == run["provenance"]["case_sha256"]
        markdown = client.get(f"/api/runs/{run['run_id']}/export?format=md").text
        assert "人工复核：unknown" in markdown
        assert "DEMO-P0002" in markdown
    assert digest(store.get_case("DEMO")) == original_hash


def test_review_cannot_confirm_without_evidence_or_use_partial_negative(store):
    run = completed_run(store)
    with TestClient(create_app(Settings(store.root))) as client:
        payload = {
            "revision": 0,
            "label_value": "1",
            "reviewer": "工程核验",
            "comment": "这是无原文证据的不合法确认。",
            "evidence": [],
            "exclusion_coverage": "",
        }
        endpoint = f"/api/runs/{run['run_id']}/reviews/B3"
        assert client.put(endpoint, json=payload).status_code == 400
        payload.update(
            label_value="0",
            evidence=[{"span_id": "DEMO-P0002", "quote": "现场负责人已发现连接销轴未安装"}],
        )
        assert client.put(endpoint, json=payload).status_code == 400


def test_provider_failure_retains_audit_without_claiming_success(store):
    class Broken:
        model = "broken-test-double"

        def complete(self, messages, tools):
            from craneassist.provider import ProviderError

            raise ProviderError("deepseek_timeout")

        def close(self):
            pass

    run = new_run(store.get_case("DEMO"), "deepseek")
    store.save_run(run)
    result = execute(store, Settings(store.root), run["run_id"], Broken())
    assert result["status"] == "provider_failed"
    assert result["error"] == "deepseek_timeout"
    assert result["candidates"] == []
    assert not result["expert_validated"]


def test_cross_origin_mutation_and_path_traversal_rejected(store):
    with TestClient(create_app(Settings(store.root))) as client:
        assert (
            client.post(
                "/api/runs",
                headers={"Origin": "https://example.org"},
                json={"case_id": "DEMO", "provider": "demo"},
            ).status_code
            == 403
        )
        assert client.get("/api/cases/bad.id").status_code == 400
    with pytest.raises(ValueError, match="invalid_id"):
        store.get_case("../outside")


def test_no_model_ground_truth_in_imported_inputs():
    root = Path(__file__).resolve().parents[1]
    directory = root / "data/cases"
    if not directory.exists():
        pytest.skip("local thesis inputs not shipped with source")
    for path in directory.glob("TC*.json"):
        case = json.loads(path.read_text("utf-8"))
        assert case["provenance"]["prior_labels_imported"] is False
        assert "candidates" not in case
        assert "label_value" not in case
        assert "reviews" not in case


def test_pasted_located_text_keeps_preface_and_footnotes(store):
    with TestClient(create_app(Settings(store.root))) as client:
        text = (
            "事故报告说明：这是需要保留的未编号前言。\n"
            "[NEW-P0001] 第一段原文说明作业背景和事实经过。\n"
            "[1] 这是一条不应被截断或丢弃的脚注。\n"
            "[NEW-P0002] 第二段原文继续说明原因分析和责任事实。"
        )
        result = client.post(
            "/api/cases",
            json={
                "case_id": "NEW",
                "title": "导入完整性测试",
                "text": text,
                "source_url": "",
                "source_type": "UNVERIFIED",
            },
        )
        assert result.status_code == 201
        paragraphs = result.json()["paragraphs"]
        assert paragraphs[0]["span_id"] == "NEW-PREFACE"
        assert "[1] 这是一条不应被截断或丢弃的脚注。" in paragraphs[1]["text"]
        assert paragraphs[2]["span_id"] == "NEW-P0002"

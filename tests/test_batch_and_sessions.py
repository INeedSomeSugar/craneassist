from __future__ import annotations

import copy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from craneassist.api import create_app
from craneassist.batch import Batches, batch_lock
from craneassist.config import Settings
from craneassist.engine import DemoProvider, execute, new_run
from craneassist.store import Store, write_json


@pytest.fixture
def sample(tmp_path):
    store = Store(tmp_path)
    store.add_case(
        {
            "case_id": "N1",
            "title": "工程测试夹具，并非真实事故",
            "source_type": "UNVERIFIED",
            "provenance": {"fixture": True, "prior_labels_imported": False},
            "paragraphs": [
                {"span_id": "DEMO-P0001", "text": "自行编写的工程测试材料。"},
                {
                    "span_id": "DEMO-P0002",
                    "text": "现场负责人已发现连接销轴未安装，仍安排班组继续顶升，未纠正该问题。",
                },
            ],
        }
    )
    return store, Batches(store)


def finish(store, settings, run_id):
    return execute(store, settings, run_id, DemoProvider(store.codebook))


def completed(store):
    run = new_run(store.get_case("N1"), "deepseek")
    store.save_run(run)
    return finish(store, Settings(store.root, api_key="fixture"), run["run_id"])


def test_batch_resume_does_not_repeat_successful_paid_analysis(sample):
    store, batches = sample
    batches.create(["N1"], "pilot")
    first = batches.run("pilot", Settings(store.root, api_key="fixture"), executor=finish)
    second = batches.run("pilot", Settings(store.root, api_key="fixture"), executor=finish)
    assert first["attempt_count"] == second["attempt_count"] == 1
    assert second["cases"][0]["structural_quote_replay_passed"]
    assert second["cases"][0]["candidate_count"] == 16
    assert second["human_classification_accuracy"] is None
    assert not second["human_independent_annotation_completed"]


def test_blind_forms_remain_blank_and_export_manifest_is_byte_hash(sample, tmp_path):
    store, batches = sample
    batches.create(["N1"], "pilot")
    batches.run("pilot", Settings(store.root, api_key="fixture"), executor=finish)
    destination = tmp_path / "package"
    batches.export("pilot", destination)
    rows = [
        json.loads(line)
        for line in (destination / "blind-human-form.jsonl").read_text("utf-8").splitlines()
    ]
    assert len(rows) == 16
    assert all(row["label_value"] is None and row["support_type"] is None for row in rows)
    assert all(row["annotation_status"] == "NOT_STARTED" for row in rows)
    assert "candidates" not in (destination / "blind-human-form.md").read_text("utf-8")
    manifest = json.loads((destination / "MANIFEST.json").read_text("utf-8"))
    for name, expected in manifest.items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected
    with pytest.raises(ValueError, match="exists"):
        batches.export("pilot", destination)


def test_input_change_prevents_batch_model_access(sample):
    store, batches = sample
    batches.create(["N1"], "pilot")
    case = store.get_case("N1")
    case["title"] += "改动"
    write_json(store.case_dir / "N1.json", case)
    with pytest.raises(ValueError, match="source_changed"):
        batches.run("pilot", Settings(store.root, api_key="fixture"), executor=finish)


def test_corrupted_snapshot_is_rejected(sample):
    store, batches = sample
    batch = batches.create(["N1"], "pilot")
    batch["inputs"]["cases"][0]["title"] = "改动"
    write_json(batches.path("pilot"), batch)
    with pytest.raises(ValueError, match="snapshot_corrupted"):
        batches.summary("pilot")


def test_batch_lock_and_duplicate_ids(sample):
    _, batches = sample
    with pytest.raises(ValueError, match="unique"):
        batches.create(["N1", "N1"], "pilot")
    path = batches.path("pilot").with_suffix(".lock")
    with batch_lock(path), pytest.raises(ValueError, match="another_process"), batch_lock(path):
        pass


def test_review_session_identity_and_history_are_separate_from_model(sample):
    store, _ = sample
    run = completed(store)
    candidate = next(row for row in run["candidates"] if row["code"] == "B3")
    snapshot = copy.deepcopy(run["candidates"])
    session = store.start_review_session(run["run_id"], "工程测试复核人")
    payload = {
        "revision": 0,
        "label_value": candidate["label_value"],
        "evidence": candidate["evidence"],
        "reviewer": "工程测试复核人",
        "comment": "仅为测试会话身份和修订留痕。",
        "exclusion_coverage": "",
        "decision": "accept",
        "session_id": session["session_id"],
    }
    saved = store.review(run["run_id"], "B3", payload)
    assert saved["candidates"] == snapshot and not saved["expert_validated"]
    with pytest.raises(ValueError, match="already_active"):
        store.start_review_session(run["run_id"], "另一人")
    wrong = {**payload, "revision": 1, "reviewer": "另一人"}
    with pytest.raises(ValueError, match="reviewer_mismatch"):
        store.review(run["run_id"], "B3", wrong)
    payload.update(revision=1, decision="modify", comment="第二次修订只用于测试保留历史。")
    saved = store.review(run["run_id"], "B3", payload)
    assert len(saved["review_history"]) == 1
    ended = store.end_review_session(run["run_id"], session["session_id"], "工程测试复核人")
    assert ended["elapsed_seconds"] >= 0 and "INCLUDING_IDLE" in ended["timing_scope"]
    assert store.end_review_session(run["run_id"], session["session_id"], "工程测试复核人") == ended
    with pytest.raises(ValueError, match="not_active"):
        store.review(run["run_id"], "B3", {**payload, "revision": 2})


def test_accept_cannot_hide_label_or_evidence_changes(sample):
    store, _ = sample
    run = completed(store)
    payload = {
        "revision": 0,
        "label_value": "unknown",
        "evidence": [],
        "reviewer": "工程测试",
        "comment": "该修改不能登记为接受原始候选。",
        "exclusion_coverage": "",
        "decision": "accept",
    }
    with pytest.raises(ValueError, match="unchanged"):
        store.review(run["run_id"], "B3", payload)


def test_missing_token_usage_is_not_reported_as_complete(sample):
    store, batches = sample

    class NoUsage(DemoProvider):
        def complete(self, messages, tools):
            result = super().complete(messages, tools)
            result["usage"] = {}
            return result

    def executor(store, settings, run_id):
        return execute(store, settings, run_id, NoUsage(store.codebook))

    batches.create(["N1"], "pilot")
    summary = batches.run("pilot", Settings(store.root, api_key="fixture"), executor=executor)
    assert summary["model_usage"]["total_tokens"] is None
    assert not summary["model_usage"]["accounting_complete"]


def test_batch_and_review_session_api_keep_validation_admission_closed(sample):
    store, batches = sample
    batches.create(["N1"], "pilot")
    batches.run("pilot", Settings(store.root, api_key="fixture"), executor=finish)
    run_id = batches.load("pilot")["attempts"]["N1"][0]
    candidate = next(row for row in store.get_run(run_id)["candidates"] if row["code"] == "B3")
    with TestClient(create_app(Settings(store.root))) as client:
        assert client.get("/api/batches").json()[0]["case_count"] == 1
        session = client.post(
            f"/api/runs/{run_id}/review-sessions", json={"reviewer": "工程测试"}
        ).json()
        payload = {
            "revision": 0,
            "label_value": candidate["label_value"],
            "reviewer": "工程测试",
            "comment": "合成工程测试，仅验证接口。",
            "evidence": candidate["evidence"],
            "decision": "accept",
            "session_id": session["session_id"],
        }
        assert client.put(f"/api/runs/{run_id}/reviews/B3", json=payload).status_code == 200
        endpoint = f"/api/runs/{run_id}/review-sessions/{session['session_id']}/end"
        assert client.put(endpoint, json={"reviewer": "其他人"}).status_code == 400
        assert client.put(endpoint, json={"reviewer": "工程测试"}).status_code == 200
        exported = client.get(f"/api/runs/{run_id}/export").json()
        assert exported["schema_version"] == "craneassist.v0.3"
        assert not exported["run"]["expert_validated"]
        assert not exported["run"]["human_independent_annotation_completed"]
        assert not exported["run"]["training_or_evaluation_admitted"]

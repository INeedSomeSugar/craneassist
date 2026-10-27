"""Frozen, resumable report batches; blind forms never contain model candidates."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import Settings
from .engine import ToolSession, execute, new_run
from .store import Store, digest, now, write_json


@contextmanager
def batch_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("batch_is_running_in_another_process") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Batches:
    def __init__(self, store: Store):
        self.store = store
        self.directory = store.root / "work" / "batches"

    def path(self, batch_id: str) -> Path:
        return self.store.path(self.directory, batch_id)

    def create(self, case_ids: list[str], batch_id: str | None = None) -> dict:
        if not case_ids or len(case_ids) != len(set(case_ids)):
            raise ValueError("batch_requires_unique_case_ids")
        cases = [self.store.get_case(key) for key in case_ids]
        if any(case["source_type"] == "SYNTHETIC" for case in cases):
            raise ValueError("research_batch_excludes_synthetic_cases")
        identity = batch_id or uuid.uuid4().hex
        target = self.path(identity)
        with batch_lock(target.with_suffix(".lock")):
            if target.exists():
                raise ValueError("batch_already_exists")
            inputs = {
                "codebook": self.store.codebook,
                "cases": cases,
                "scope": "SAVED_REPORTS_AND_CODEBOOK_ONLY_NO_PRIOR_LABELS",
            }
            if any(
                key in case for case in cases for key in ("candidates", "reviews", "label_value")
            ):
                raise ValueError("prior_labels_not_allowed_in_batch_inputs")
            batch = {
                "batch_id": identity,
                "created_at": now(),
                "inputs": inputs,
                "input_sha256": digest(inputs),
                "attempts": {key: [] for key in case_ids},
                "expert_validated": False,
                "training_or_evaluation_admitted": False,
                "human_independent_annotation_completed": False,
            }
            write_json(target, batch)
            return batch

    def load(self, batch_id: str) -> dict:
        batch = json.loads(self.path(batch_id).read_text("utf-8"))
        if digest(batch["inputs"]) != batch["input_sha256"]:
            raise ValueError("batch_input_snapshot_corrupted")
        if digest(self.store.codebook) != digest(batch["inputs"]["codebook"]):
            raise ValueError("codebook_changed_since_batch_creation")
        for case in batch["inputs"]["cases"]:
            if digest(self.store.get_case(case["case_id"])) != digest(case):
                raise ValueError("source_changed_since_batch_creation")
        return batch

    def run(
        self, batch_id: str, settings: Settings, *, retry_failed: bool = False, executor=execute
    ) -> dict:
        with batch_lock(self.path(batch_id).with_suffix(".lock")):
            batch = self.load(batch_id)
            if not settings.api_key:
                raise ValueError("missing_deepseek_api_key")
            for case in batch["inputs"]["cases"]:
                attempts = batch["attempts"][case["case_id"]]
                if attempts:
                    latest = self.store.get_run(attempts[-1])
                    if latest["status"] == "ready_for_review" or not retry_failed:
                        continue
                self.load(batch_id)  # Recheck inputs before every paid report analysis.
                run = new_run(case, "deepseek")
                run["batch_id"] = batch_id
                self.store.save_run(run)
                attempts.append(run["run_id"])
                write_json(self.path(batch_id), batch)
                result = executor(self.store, settings, run["run_id"])
                print(f"{case['case_id']}: {result['status']}", flush=True)
            return self.summary(batch_id)

    def summary(self, batch_id: str) -> dict:
        batch = self.load(batch_id)
        results, all_runs = [], []
        for case in batch["inputs"]["cases"]:
            runs = [self.store.get_run(key) for key in batch["attempts"][case["case_id"]]]
            if any(
                run["case_id"] != case["case_id"]
                or run["provenance"]["case_sha256"] != case["content_sha256"]
                or run["provider"] != "deepseek"
                for run in runs
            ):
                raise ValueError("batch_attempt_source_or_provider_mismatch")
            all_runs.extend(runs)
            latest = runs[-1] if runs else None
            audit_ok = False
            if latest and latest["status"] == "ready_for_review":
                session = ToolSession(case, batch["inputs"]["codebook"])
                for event in latest["events"]:
                    if event["type"] == "tool":
                        if session.call(event["name"], event["args"]) != event["result"]:
                            raise ValueError("batch_tool_replay_mismatch")
                if session.accepted != latest["candidates"] or not session.audit.get("ok"):
                    raise ValueError("batch_candidate_submission_mismatch")
                audit_ok = True
            reviews = list(latest.get("reviews", {}).values()) if latest else []
            results.append(
                {
                    "case_id": case["case_id"],
                    "source_type": case["source_type"],
                    "paragraph_count": len(case["paragraphs"]),
                    "attempt_count": len(runs),
                    "run_id": latest["run_id"] if latest else None,
                    "status": latest["status"] if latest else "not_started",
                    "structural_quote_replay_passed": audit_ok,
                    "candidate_count": len(latest["candidates"]) if latest else 0,
                    "reviewed_count": len(reviews),
                    "decisions": {
                        value: sum(row.get("decision") == value for row in reviews)
                        for value in ("accept", "modify", "reject")
                    },
                    "finished_review_session_seconds": sum(
                        row.get("elapsed_seconds", 0)
                        for row in latest.get("review_sessions", [])
                        if row.get("ended_at")
                    )
                    if latest
                    else None,
                }
            )
        complete = all(run.get("usage", {}).get("accounting_complete", True) for run in all_runs)
        observed = sum(run["usage"]["total_tokens"] for run in all_runs)
        return {
            "schema_version": "craneassist.batch.v0.2",
            "batch_id": batch_id,
            "input_sha256": batch["input_sha256"],
            "case_count": len(results),
            "attempt_count": len(all_runs),
            "cases": results,
            "model_usage": {
                "observed_tokens": observed,
                "total_tokens": observed if complete else None,
                "accounting_complete": complete,
                "elapsed_seconds": round(sum(r.get("elapsed_seconds", 0) for r in all_runs), 3),
            },
            "human_classification_accuracy": None,
            "expert_validated": False,
            "human_independent_annotation_completed": False,
            "training_or_evaluation_admitted": False,
            "notice": "引用与工具重放检查不代表HFACS分类正确性；计时为会话历时，包含停留。",
        }

    def export(self, batch_id: str, output: Path) -> dict:
        batch = self.load(batch_id)
        if output.exists():
            raise ValueError("batch_export_directory_exists")
        summary = self.summary(batch_id)
        output.mkdir(parents=True)
        write_json(output / "batch-summary.json", summary)
        write_json(output / "input-snapshot.json", batch["inputs"])
        lines = [
            "# 独立人工初标空表",
            "",
            "本文件只含原文和代码本，不含AI候选。空表不是人工标注或专家真值。",
            "建议在查看AI候选前完成；导出时间本身不能证明人工独立性。",
            "",
        ]
        blank = []
        for case in batch["inputs"]["cases"]:
            lines.extend(
                [
                    f"## {case['case_id']} · {case['title']}",
                    "",
                    f"来源：{case.get('source_url', '')}",
                    f"正文SHA256：{case['content_sha256']}",
                    "",
                ]
            )
            lines.extend(f"[{p['span_id']}] {p['text']}\n" for p in case["paragraphs"])
            for code in batch["inputs"]["codebook"]:
                lines.extend(
                    [
                        f"### {code['code']} {code['name_zh']}",
                        code["inclusion"],
                        code["boundary"],
                        "人工判断：____；证据编号及摘录：____；说明：____",
                        "",
                    ]
                )
                blank.append(
                    {
                        "case_id": case["case_id"],
                        "code": code["code"],
                        "case_sha256": case["content_sha256"],
                        "label_value": None,
                        "support_type": None,
                        "evidence": [],
                        "reviewer": "",
                        "annotation_status": "NOT_STARTED",
                    }
                )
        (output / "blind-human-form.md").write_text("\n".join(lines), "utf-8")
        (output / "blind-human-form.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in blank), "utf-8"
        )
        ai_runs = {
            key: self.store.get_run(attempts[-1])
            for key, attempts in batch["attempts"].items()
            if attempts
        }
        write_json(
            output / "ai-assisted-review.json", {"runs": ai_runs, "notice": summary["notice"]}
        )
        assisted = ["# AI候选与原文证据复核稿", "", summary["notice"], ""]
        for case_id, run in ai_runs.items():
            assisted.extend(
                [
                    f"## {case_id} · {run['status']}",
                    "",
                    f"运行：{run['run_id']}；正文SHA256：{run['provenance']['case_sha256']}",
                    "",
                ]
            )
            for row in run["candidates"]:
                assisted.extend(
                    [
                        f"### {row['code']} · 候选 {row['label_value']}",
                        "",
                        f"证据支持：{row['support_type']} / {row['evidence_relation']}",
                        f"事实命题：{row['fact']}",
                        f"判断说明：{row['rationale']}",
                        f"类别边界：{row['boundary_note']}",
                        "",
                    ]
                )
                assisted.extend(f"> [{e['span_id']}] {e['quote']}\n" for e in row["evidence"])
                assisted.append("人工复核：待实际填写；接受／修改／驳回：____。\n")
        (output / "ai-assisted-review.md").write_text("\n".join(assisted), "utf-8")
        # Use actual byte SHA-256 for portable verification.
        import hashlib

        manifest = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.iterdir())
        }
        write_json(output / "MANIFEST.json", manifest)
        return summary

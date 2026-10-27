from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def mark_sections(paragraphs: list[dict]) -> list[dict]:
    section = "report"
    result = []
    for item in paragraphs:
        text = item["text"]
        if len(text) < 85:
            if re.search(r"(防范.{0,6}措施|整改.{0,4}建议|防范.{0,5}建议|事故防范)", text):
                section = "recommendations"
            elif re.search(r"(事故经过|直接原因|间接原因|原因分析|原因及性质|责任认定)", text):
                section = "report"
        result.append({"span_id": item["span_id"], "text": text, "section": section})
    return result


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()
        self.case_dir = root / "data" / "cases"
        self.run_dir = root / "work" / "runs"
        self.codebook = json.loads(
            (Path(__file__).parent / "resources/codebook.json").read_text("utf-8-sig")
        )
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def path(self, directory: Path, key: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", key):
            raise ValueError("invalid_id")
        return directory / f"{key}.json"

    def cases(self) -> list[dict]:
        return [self.get_case(p.stem) for p in sorted(self.case_dir.glob("*.json"))]

    def get_case(self, key: str) -> dict:
        case = json.loads(self.path(self.case_dir, key).read_text("utf-8"))
        if case["content_sha256"] != digest(case["paragraphs"]):
            raise ValueError("case_content_hash_mismatch")
        return case

    def add_case(self, case: dict) -> dict:
        with self.lock:
            target = self.path(self.case_dir, case["case_id"])
            if target.exists():
                raise ValueError("case_id_exists")
            case["paragraphs"] = mark_sections(case["paragraphs"])
            ids = [p["span_id"] for p in case["paragraphs"]]
            if len(ids) != len(set(ids)) or not ids:
                raise ValueError("duplicate_or_empty_span_ids")
            case["content_sha256"] = digest(case["paragraphs"])
            text_hash = digest([p["text"] for p in case["paragraphs"]])
            for existing in self.cases():
                if digest([p["text"] for p in existing["paragraphs"]]) == text_hash:
                    raise ValueError("duplicate_report_content")
            case["imported_at"] = now()
            write_json(target, case)
            return case

    def save_run(self, run: dict) -> None:
        with self.lock:
            write_json(self.path(self.run_dir, run["run_id"]), run)

    def get_run(self, key: str) -> dict:
        return json.loads(self.path(self.run_dir, key).read_text("utf-8"))

    def runs(self) -> list[dict]:
        return sorted(
            [json.loads(p.read_text("utf-8")) for p in self.run_dir.glob("*.json")],
            key=lambda r: r["created_at"],
            reverse=True,
        )

    def review(self, run_id: str, code: str, payload: dict) -> dict:
        with self.lock:
            run = self.get_run(run_id)
            if run["status"] != "ready_for_review":
                raise ValueError("run_not_ready_for_review")
            if code not in {c["code"] for c in run["candidates"]}:
                raise ValueError("unknown_code")
            if payload["revision"] != run["review_revision"]:
                raise ValueError("review_conflict_refresh_required")
            case = self.get_case(run["case_id"])
            if case["content_sha256"] != run["provenance"]["case_sha256"]:
                raise ValueError("source_changed_since_run")
            candidate = next(row for row in run["candidates"] if row["code"] == code)
            if payload.get("decision") == "accept" and (
                payload["label_value"] != candidate["label_value"]
                or payload["evidence"] != candidate["evidence"]
            ):
                raise ValueError("accept_requires_unchanged_label_and_evidence_use_modify")
            if payload.get("session_id"):
                sessions = [
                    item
                    for item in run.get("review_sessions", [])
                    if item["session_id"] == payload["session_id"]
                ]
                if (
                    len(sessions) != 1
                    or sessions[0].get("ended_at")
                    or sessions[0]["reviewer"] != payload["reviewer"]
                ):
                    raise ValueError("review_session_not_active_or_reviewer_mismatch")
            if code in run["reviews"]:
                run.setdefault("review_history", []).append({"code": code, **run["reviews"][code]})
            run["reviews"][code] = {
                **payload,
                "reviewed_at": now(),
                "review_mode": "HUMAN_REVIEW_AFTER_AI",
            }
            run["review_revision"] += 1
            self.save_run(run)
            return run

    def start_review_session(self, run_id: str, reviewer: str) -> dict:
        with self.lock:
            run = self.get_run(run_id)
            if run["status"] != "ready_for_review" or not reviewer.strip():
                raise ValueError("review_session_requires_ready_run_and_reviewer")
            if any(not item.get("ended_at") for item in run.get("review_sessions", [])):
                raise ValueError("review_session_already_active")
            session = {
                "session_id": uuid.uuid4().hex,
                "reviewer": reviewer.strip(),
                "started_at": now(),
                "ended_at": None,
                "elapsed_seconds": None,
                "timing_scope": "WALL_CLOCK_SESSION_INCLUDING_IDLE_NOT_ACTIVE_WORK",
            }
            run.setdefault("review_sessions", []).append(session)
            self.save_run(run)
            return session

    def end_review_session(self, run_id: str, session_id: str, reviewer: str) -> dict:
        with self.lock:
            run = self.get_run(run_id)
            sessions = [
                row for row in run.get("review_sessions", []) if row["session_id"] == session_id
            ]
            if len(sessions) != 1 or sessions[0]["reviewer"] != reviewer.strip():
                raise ValueError("review_session_not_found_or_reviewer_mismatch")
            session = sessions[0]
            if not session["ended_at"]:
                session["ended_at"] = now()
                session["elapsed_seconds"] = max(
                    0,
                    round(
                        (
                            datetime.fromisoformat(session["ended_at"])
                            - datetime.fromisoformat(session["started_at"])
                        ).total_seconds(),
                        3,
                    ),
                )
                self.save_run(run)
            return session

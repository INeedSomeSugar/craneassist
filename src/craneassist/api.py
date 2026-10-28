from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .engine import execute, new_run
from .evidence import check_evidence
from .models import ImportRequest, ReviewRequest, ReviewSessionRequest, RunRequest
from .review import REVIEW_REASONS, review_projection
from .store import Store


def brief_run(run: dict) -> dict:
    return {
        k: v
        for k, v in run.items()
        if k not in {"events", "messages", "candidates", "reviews", "codebook_snapshot"}
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.root)
    for run in store.runs():
        if run["status"] in {"running", "queued"}:
            run["status"] = "interrupted"
            run["error"] = "service_restarted_start_a_new_run"
            store.save_run(run)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="craneassist")
    app = FastAPI(title="CraneAssist", version="0.3.0", docs_url="/docs")
    app.state.store = store

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "local_host_required"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "same_origin_required"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing(request: Request, exc: FileNotFoundError):
        return JSONResponse({"detail": "record_not_found"}, status_code=404)

    @app.get("/api/config")
    def config():
        return {
            "deepseek_configured": bool(settings.api_key),
            "model": settings.model,
            "max_turns": settings.max_turns,
            "token_budget": settings.token_budget,
            "codebook": store.codebook,
            "review_reasons": REVIEW_REASONS,
        }

    @app.get("/api/cases")
    def cases():
        return [
            {k: v for k, v in case.items() if k != "paragraphs"}
            | {"paragraph_count": len(case["paragraphs"])}
            for case in store.cases()
        ]

    @app.get("/api/batches")
    def batches():
        from .batch import Batches

        manager = Batches(store)
        return [manager.summary(path.stem) for path in sorted(manager.directory.glob("*.json"))]

    @app.post("/api/runs/{run_id}/review-sessions", status_code=201)
    def start_review_session(run_id: str, payload: ReviewSessionRequest):
        return store.start_review_session(run_id, payload.reviewer)

    @app.put("/api/runs/{run_id}/review-sessions/{session_id}/end")
    def end_review_session(run_id: str, session_id: str, payload: ReviewSessionRequest):
        return store.end_review_session(run_id, session_id, payload.reviewer)

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str):
        return store.get_case(case_id)

    @app.post("/api/cases", status_code=201)
    def import_case(payload: ImportRequest):
        if payload.source_url:
            url = urlsplit(payload.source_url)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username:
                raise ValueError("invalid_source_url")
        markers = list(re.finditer(r"(?m)^\[([^\]\n]+-P\d+)\][ \t]*", payload.text))
        if markers:
            paragraphs = []
            prefix = payload.text[: markers[0].start()].strip()
            if prefix:
                paragraphs.append({"span_id": f"{payload.case_id}-PREFACE", "text": prefix})
            for index, marker in enumerate(markers):
                end = markers[index + 1].start() if index + 1 < len(markers) else len(payload.text)
                text = payload.text[marker.end() : end].strip()
                if text:
                    paragraphs.append({"span_id": marker.group(1), "text": text})
        else:
            paragraphs = [
                {"span_id": f"{payload.case_id}-P{i:04d}", "text": text.strip()}
                for i, text in enumerate(payload.text.splitlines(), 1)
                if text.strip()
            ]
        return store.add_case(
            {
                "case_id": payload.case_id,
                "duplicate_group_id": payload.case_id,
                "title": payload.title,
                "source_url": payload.source_url,
                "source_type": payload.source_type,
                "paragraphs": paragraphs,
                "provenance": {
                    "import_mode": "USER_PASTED_TEXT",
                    "source_type_is_user_supplied": True,
                    "official_origin_reverified_live": False,
                    "raw_text_sha256": hashlib.sha256(payload.text.encode()).hexdigest(),
                    "prior_labels_imported": False,
                },
            }
        )

    @app.get("/api/runs")
    def runs():
        return [
            brief_run(run)
            | {"candidate_count": len(run["candidates"]), "reviewed_count": len(run["reviews"])}
            for run in store.runs()
        ]

    @app.post("/api/runs", status_code=202)
    def start_run(payload: RunRequest):
        case = store.get_case(payload.case_id)
        if payload.provider == "deepseek" and not settings.api_key:
            raise HTTPException(400, "missing_deepseek_api_key")
        if payload.provider == "demo" and payload.case_id != "DEMO":
            raise HTTPException(400, "demo_only_supports_synthetic_DEMO_case")
        with store.lock:
            active = [r for r in store.runs() if r["status"] in {"queued", "running"}]
            if len(active) >= 3 or any(r["case_id"] == payload.case_id for r in active):
                raise HTTPException(409, "run_already_active_or_queue_full")
            run = new_run(case, payload.provider)
            store.save_run(run)
            pool.submit(execute, store, settings, run["run_id"])
        return brief_run(run)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, compact: bool = False):
        run = store.get_run(run_id)
        return (
            brief_run(run) | {"event_count": len(run["events"])}
            if compact
            else run | review_projection(run)
        )

    @app.put("/api/runs/{run_id}/reviews/{code}")
    def review(run_id: str, code: str, payload: ReviewRequest):
        run = store.get_run(run_id)
        case = store.get_case(run["case_id"])
        if run["provenance"]["case_sha256"] != case["content_sha256"]:
            raise ValueError("source_changed_since_run")
        errors = check_evidence([e.model_dump() for e in payload.evidence], case)
        if errors:
            raise ValueError(";".join(errors))
        if payload.label_value in {"1", "0"} and not payload.evidence:
            raise ValueError("human_1_or_0_requires_exact_evidence")
        if payload.label_value == "0" and len(payload.exclusion_coverage.strip()) < 40:
            raise ValueError("0_requires_explaining_complete_core_condition_exclusion_min_40_chars")
        reviewed = store.review(run_id, code, payload.model_dump())
        return reviewed | review_projection(reviewed)

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str, format: str = "json"):
        run = store.get_run(run_id)
        case = store.get_case(run["case_id"])
        export_data = {
            "schema_version": "craneassist.v0.3",
            "case": case,
            "run": run,
            **review_projection(run),
            "notice": "AI候选及看过AI后的人工复核。未建立独立标注或专家真值。",
        }
        if format == "json":
            content = json.dumps(export_data, ensure_ascii=False, indent=2)
            media = "application/json"
            extension = "json"
        elif format == "md":
            lines = [
                f"# {case['case_id']} {case['title']}",
                "",
                export_data["notice"],
                "",
                f"来源：{case.get('source_url', '')}",
                f"来源类型：{case['source_type']}",
                f"正文SHA256：{case['content_sha256']}",
                f"运行状态：{run['status']}",
                "",
            ]
            for row in run["candidates"]:
                lines += [
                    f"## {row['code']} · AI候选 {row['label_value']}",
                    "",
                    f"证据关系：{row['support_type']} / {row['evidence_relation']}",
                    f"事实：{row['fact']}",
                    f"判断：{row['rationale']}",
                    f"边界：{row['boundary_note']}",
                    "",
                ]
                for ev in row["evidence"]:
                    lines += [f"[{ev['span_id']}] {ev['quote']}", ""]
                human = run["reviews"].get(row["code"])
                if human:
                    lines += [
                        f"人工复核：{human['label_value']}；复核人：{human['reviewer']}",
                        f"意见：{human['comment']}",
                        "复核原因："
                        + "、".join(
                            REVIEW_REASONS.get(reason, reason)
                            for reason in human.get("reason_codes", [])
                        ),
                        "",
                    ]
                    for ev in human["evidence"]:
                        lines += [f"人工证据 [{ev['span_id']}] {ev['quote']}", ""]
                    if human["exclusion_coverage"]:
                        lines += [f"排除覆盖说明：{human['exclusion_coverage']}", ""]
                else:
                    lines += ["人工复核：待完成。", ""]
            content, media, extension = "\n".join(lines), "text/markdown", "md"
        else:
            raise ValueError("format_must_be_json_or_md")
        return Response(
            content,
            media_type=media,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{case["case_id"]}_{run_id[:8]}.{extension}"'
                )
            },
        )

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app

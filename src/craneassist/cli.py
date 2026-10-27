from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .engine import execute, new_run
from .provider import DeepSeekProvider
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="CraneAssist 塔吊事故HFACS辅助标注")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8012)
    sub.add_parser("doctor")
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("--case", required=True)
    run.add_argument("--provider", choices=["deepseek", "demo"], default="deepseek")
    batch = sub.add_parser("batch")
    batch.add_argument("action", choices=["create", "run", "status", "export"])
    batch.add_argument("--id", required=True)
    batch.add_argument("--cases", nargs="+")
    batch.add_argument("--output", type=Path)
    batch.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env(args.root)
    if args.command == "batch":
        from .batch import Batches

        batches = Batches(Store(settings.root))
        if args.action == "create":
            if not args.cases:
                parser.error("batch create requires --cases")
            batches.create(args.cases, args.id)
            result = batches.summary(args.id)
        elif args.action == "run":
            result = batches.run(args.id, settings, retry_failed=args.retry_failed)
        elif args.action == "export":
            if not args.output:
                parser.error("batch export requires --output; existing directories are protected")
            result = batches.export(args.id, args.output)
        else:
            result = batches.summary(args.id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)
    elif args.command == "doctor":
        result = {
            "deepseek_key_present": bool(settings.api_key),
            "configured_model": settings.model,
        }
        if settings.api_key:
            provider = DeepSeekProvider(settings)
            try:
                result["catalog"] = provider.discover()
                result["selected_model"] = provider.model
            finally:
                provider.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        store = Store(settings.root)
        if args.command == "list":
            print(
                json.dumps(
                    [{"case_id": c["case_id"], "title": c["title"]} for c in store.cases()],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            record = new_run(store.get_case(args.case), args.provider)
            store.save_run(record)
            record = execute(store, settings, record["run_id"])
            print(
                json.dumps(
                    {
                        k: record.get(k)
                        for k in ["run_id", "status", "model", "usage", "elapsed_seconds", "error"]
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()

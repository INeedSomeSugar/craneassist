"""Re-run all recorded tools locally; no model call or credentials are needed."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from craneassist.engine import ToolSession
from craneassist.store import Store, digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    store = Store(args.root)
    run = store.get_run(args.run_id)
    case = store.get_case(run["case_id"])
    assert case["content_sha256"] == run["provenance"]["case_sha256"], "source hash changed"
    codebook = run["codebook_snapshot"]
    assert digest(codebook) == run["provenance"]["codebook_sha256"], "codebook hash changed"
    session = ToolSession(case, codebook)
    tool_count = 0
    for event in run["events"]:
        if event["type"] == "tool":
            tool_count += 1
            result = session.call(event["name"], event["args"])
            assert result == event["result"], f"tool replay differs: {event['name']}"
    assert session.accepted == run["candidates"], "candidates differ from tool submission"
    assert session.audit["ok"], "submission did not pass structural checks"
    assert not run["expert_validated"]
    assert not run["training_or_evaluation_admitted"]
    if case["provenance"].get("original_path"):
        path = Path(case["provenance"]["original_path"])
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest() == case["provenance"]["original_sha256"]
        )
    print(
        json.dumps(
            {
                "ok": True,
                "tool_events_replayed": tool_count,
                "candidate_count": len(run["candidates"]),
                "coverage": session.audit["coverage"],
                "scope": "ENGINEERING_TRACE_AND_QUOTES_ONLY",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

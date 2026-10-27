"""Create only the synthetic teaching case; does not read thesis files or call a model."""

from __future__ import annotations

import argparse
from pathlib import Path

from craneassist.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    store = Store(args.root)
    if (store.case_dir / "DEMO.json").exists():
        case = store.get_case("DEMO")
        if case.get("source_type") != "SYNTHETIC":
            raise ValueError("existing_DEMO_is_not_synthetic")
        print("Synthetic DEMO already exists; preserved.")
        return
    store.add_case(
        {
            "case_id": "DEMO",
            "title": "合成教学例：已知隐患未纠正",
            "source_type": "SYNTHETIC",
            "source_url": "",
            "provenance": {"synthetic": True, "prior_labels_imported": False},
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
    print("Created synthetic DEMO; no model calls or thesis imports.")


if __name__ == "__main__":
    main()

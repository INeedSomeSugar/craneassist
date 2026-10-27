"""Read existing reports/codebook, verify body hashes, copy only unlabelled inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from craneassist.store import Store, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("D:/毕业论文"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    folder = args.source / "事故数据/专题报告规范标注_v1.0"
    source = folder / "prepared_cases.json"
    rows = json.loads(source.read_text("utf-8-sig"))
    codebook_path = folder / "分类规则_v1.0.json"
    codebook = json.loads(codebook_path.read_text("utf-8-sig"))
    if len(codebook) != 16 or {x["code"] for x in codebook} != set(
        "A1 A2 A3 B1 B2 B3 B4 C1 C2 C3 C4 C5 D1 D2 D3 D4".split()
    ):
        raise ValueError("unexpected_codebook")
    resources = args.root / "src/craneassist/resources"
    write_json(resources / "codebook.json", codebook)
    write_json(
        resources / "codebook_provenance.json",
        {
            "version": "zhou_lei_2018_hfacs16_tower_annotation_v1.0",
            "source_path": str(codebook_path),
            "source_sha256": hashlib.sha256(codebook_path.read_bytes()).hexdigest(),
            "scope": "Names confirmed; operational boundaries pending human review",
        },
    )
    store = Store(args.root)
    imported = []
    for row in rows:
        path = args.source / row["annotation_body_path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != row["annotation_body_sha256"]:
            raise ValueError(f"body_hash_mismatch:{row['case_id']}")
        located = re.findall(
            r"\[([^\]]+-P\d+)\]\s*(.*?)(?=\n\[|\Z)", path.read_text("utf-8-sig"), re.S
        )
        if [(span, text.strip()) for span, text in located] != [
            (p["span_id"], p["text"]) for p in row["paragraphs"]
        ]:
            raise ValueError(f"prepared_paragraphs_differ_from_body:{row['case_id']}")
        original = args.source / row["source_located_path"]
        if hashlib.sha256(original.read_bytes()).hexdigest() != row["source_located_sha256"]:
            raise ValueError(f"original_text_hash_mismatch:{row['case_id']}")
        case = {
            "case_id": row["case_id"],
            "duplicate_group_id": row["case_id"],
            "title": row["name"],
            "date": row["date"],
            "mechanism": row["mechanism"],
            "source_url": row["source_url"],
            "source_type": row["source_type"],
            "paragraphs": row["paragraphs"],
            "provenance": {
                "import_mode": "LOCAL_SAVED_TEXT_HASH_CHECKED",
                "original_path": str(original),
                "original_sha256": row["source_located_sha256"],
                "body_path": str(path),
                "body_sha256": actual,
                "upstream_source_type": row["source_type"],
                "source_status": row.get("status", ""),
                "official_origin_reverified_live": False,
                "source_index_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "prior_labels_imported": False,
            },
        }
        target = store.case_dir / f"{row['case_id']}.json"
        if target.exists():
            existing = store.get_case(row["case_id"])
            if existing["provenance"].get("body_sha256") != actual:
                raise ValueError(f"existing_case_differs:{row['case_id']}")
        else:
            store.add_case(case)
        imported.append(row["case_id"])
    demo = {
        "case_id": "DEMO",
        "title": "合成教学例：已知隐患未纠正",
        "source_type": "SYNTHETIC",
        "source_url": "",
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
    if not (store.case_dir / "DEMO.json").exists():
        store.add_case(demo)
    write_json(
        args.root / "data/import_manifest.json",
        {
            "case_ids": imported,
            "count": len(imported),
            "prior_labels_imported": False,
            "original_files_modified": False,
            "codebook_source": str(codebook_path),
        },
    )
    print(
        json.dumps({"imported": len(imported), "prior_labels_imported": False}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()

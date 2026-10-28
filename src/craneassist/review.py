"""Deterministic review navigation; priority is a work order, never semantic confidence."""

from collections import Counter

REVIEW_REASONS = {
    "fact_missing": "事故事实缺失或不符",
    "category_confusion": "类别或层级混淆",
    "evidence_insufficient": "引文不足以支持判断",
    "postaccident_misuse": "误用事后整改或建议",
    "unsupported_inference": "推理超出原文",
    "other": "其他原因（见意见）",
}


def review_projection(run: dict) -> dict:
    warnings = run.get("audit", {}).get("warnings", [])
    reviews = run.get("reviews", {})
    rows = []
    for candidate in run.get("candidates", []):
        code = candidate["code"]
        reasons = []
        if candidate.get("evidence_relation") == "indirect":
            reasons.append("间接证据，需要核对推理步骤")
        for warning in warnings:
            codes, _, kind = warning.partition(":")
            if code not in codes.split("/"):
                continue
            if kind == "priority_human_boundary_review":
                reasons.append("重点核查类别边界")
            elif kind == "shared_passage_check_distinct_facts":
                reasons.append("与其他类别共享段落，需要区分事实命题")
        if candidate.get("support_type") == "related_insufficient":
            reasons.append("存在相关材料，但支持条件不足")
        reasons = list(dict.fromkeys(reasons))
        rows.append(
            {
                "code": code,
                "priority": len(reasons),
                "reasons": reasons,
                "reviewed": code in reviews,
            }
        )
    rows.sort(key=lambda row: (row["reviewed"], -row["priority"], row["code"]))
    counts = Counter(reason for row in reviews.values() for reason in row.get("reason_codes", []))
    return {
        "review_queue": rows,
        "review_summary": {
            "pending": sum(not row["reviewed"] for row in rows),
            "priority_pending": sum(not row["reviewed"] and row["priority"] > 0 for row in rows),
            "reason_counts": {key: counts[key] for key in REVIEW_REASONS},
            "reviews_without_reason": sum(not row.get("reason_codes") for row in reviews.values()),
            "notice": "提示按证据关系和结构警告排列，不是模型置信度或分类正确率。",
        },
    }

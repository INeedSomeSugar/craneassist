from __future__ import annotations

from .models import Submission


def check_evidence(evidence: list[dict], case: dict) -> list[str]:
    paragraphs = {p["span_id"]: p for p in case["paragraphs"]}
    errors = []
    for ev in evidence:
        paragraph = paragraphs.get(ev["span_id"])
        if paragraph is None:
            errors.append(f"unknown_span:{ev['span_id']}")
        elif ev["quote"] not in paragraph["text"]:
            errors.append(f"quote_not_exact:{ev['span_id']}")
        elif paragraph["section"] == "recommendations":
            errors.append(f"recommendation_cannot_prove_preaccident_fact:{ev['span_id']}")
    return errors


def validate_submission(
    submission: Submission,
    case: dict,
    codebook: list[dict],
    read_ids: set[str],
    codebook_read: bool,
) -> dict:
    rows = [row.model_dump() for row in submission.candidates]
    errors = []
    warnings = []
    expected = {r["code"] for r in codebook}
    actual = [r["code"] for r in rows]
    if set(actual) != expected or len(set(actual)) != 16:
        errors.append("must_cover_each_of_the_16_codes_exactly_once")
    if not codebook_read:
        errors.append("read_codebook_before_submission")
    unread = [p["span_id"] for p in case["paragraphs"] if p["span_id"] not in read_ids]
    if unread:
        errors.append(f"unread_passages:{len(unread)}; read all passages using read_passages")
    for row in rows:
        code = row["code"]
        errors.extend(f"{code}:{err}" for err in check_evidence(row["evidence"], case))
        if row["label_value"] == "1":
            if row["support_type"] != "supports" or not row["evidence"] or not row["fact"]:
                errors.append(f"{code}:positive_requires_support_fact_and_exact_evidence")
            if row["evidence_relation"] == "not_applicable":
                errors.append(f"{code}:positive_requires_evidence_relation")
        else:
            if row["support_type"] == "supports":
                errors.append(f"{code}:unknown_cannot_have_supports")
            if row["support_type"] == "related_insufficient" and not row["evidence"]:
                errors.append(f"{code}:related_insufficient_requires_evidence")
        if code in {"B3", "B4", "C1", "D1", "D4"} and row["label_value"] == "1":
            warnings.append(f"{code}:priority_human_boundary_review")
    for index, row in enumerate(rows):
        for other in rows[index + 1 :]:
            a = {ev["span_id"] for ev in row["evidence"]}
            b = {ev["span_id"] for ev in other["evidence"]}
            if a & b and row["label_value"] == other["label_value"] == "1":
                warnings.append(
                    f"{row['code']}/{other['code']}:shared_passage_check_distinct_facts"
                )
                if {row["code"], other["code"]} == {"D1", "D4"}:
                    if row["fact"].strip() == other["fact"].strip():
                        errors.append("D1/D4:same_act_cannot_be_confirmed_as_both")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": sorted(set(warnings)),
        "coverage": {"read": len(read_ids), "total": len(case["paragraphs"])},
        "scope": "STRUCTURAL_AND_EXACT_QUOTE_CHECKS_NOT_EXPERT_VALIDATION",
    }

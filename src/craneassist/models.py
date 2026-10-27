from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StrictModel):
    span_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=8, max_length=5000)


class Candidate(StrictModel):
    code: str = Field(pattern=r"^[ABCD][1-5]$")
    label_value: Literal["1", "unknown"]
    support_type: Literal["supports", "related_insufficient", "insufficient_material"]
    evidence_relation: Literal["direct", "indirect", "not_applicable"]
    fact: str = Field(max_length=1600)
    rationale: str = Field(min_length=8, max_length=2500)
    boundary_note: str = Field(max_length=1600)
    evidence: list[Evidence] = Field(max_length=10)


class Submission(StrictModel):
    candidates: list[Candidate] = Field(min_length=16, max_length=16)


class ReadArgs(StrictModel):
    offset: int = Field(ge=0, le=10000)
    limit: int = Field(ge=1, le=30)


class SearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=80)


class EmptyArgs(StrictModel):
    pass


class RunRequest(StrictModel):
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    provider: Literal["deepseek", "demo"] = "deepseek"


class ImportRequest(StrictModel):
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    title: str = Field(min_length=1, max_length=250)
    text: str = Field(min_length=50, max_length=180000)
    source_url: str = Field(default="", max_length=2000)
    source_type: Literal["OFFICIAL_HTML_OR_PDF", "REPOST_HTML", "UNVERIFIED"] = "UNVERIFIED"


class ReviewRequest(StrictModel):
    revision: int = Field(ge=0)
    label_value: Literal["1", "0", "unknown"]
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str = Field(min_length=8, max_length=3000)
    evidence: list[Evidence] = Field(max_length=10)
    exclusion_coverage: str = Field(default="", max_length=3000)
    decision: Literal["accept", "modify", "reject"] | None = None
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ReviewSessionRequest(StrictModel):
    reviewer: str = Field(min_length=1, max_length=100)

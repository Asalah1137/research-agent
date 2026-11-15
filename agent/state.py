"""Pydantic models for the data passed between research nodes."""

from typing import Literal

from pydantic import BaseModel, Field


class ResearchPlan(BaseModel):
    scope: str = Field(min_length=1)
    subquestions: list[str] = Field(min_length=1)
    queries: list[str] = Field(min_length=3, max_length=4)


class Source(BaseModel):
    number: int = Field(ge=1)
    title: str
    url: str
    text: str = Field(min_length=1)


class Critique(BaseModel):
    missing_aspects: list[str]
    unsupported_claims: list[str]
    passed: bool


class TargetedQueries(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=2)


class ResearchState(BaseModel):
    question: str = Field(min_length=1)
    plan: ResearchPlan | None = None
    sources: list[Source] = Field(default_factory=list)
    report: str = ""
    critique: Critique | None = None
    citation_errors: list[int] = Field(default_factory=list)
    # Only rewriting a previous report increases this counter.
    refinement_rounds: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)
    status: Literal["success", "limited"] = "limited"
    limitations: list[str] = Field(default_factory=list)
    error: str = ""

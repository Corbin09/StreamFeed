"""Attributes of a job post (category "job")."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Level = Literal["intern", "fresher", "junior", "mid", "senior", "lead", "manager"]
WorkMode = Literal["onsite", "hybrid", "remote"]


class JobAttributes(BaseModel):
    model_config = ConfigDict(extra="ignore")

    levels: list[Level] = Field(default_factory=list, description="Seniority levels in the title")
    skills: list[str] = Field(default_factory=list, description="Canonical skill keys")
    locations: list[str] = Field(default_factory=list, description="All canonical locations")
    work_mode: WorkMode | None = None
    salary_negotiable: bool = False
    salary_text: str | None = Field(None, description="Salary as written by the source")
    employment_type: str | None = None  # e.g. FULL_TIME
    expertise: str | None = None  # source's job category, e.g. "Backend Developer"
    experience_months: int | None = None
    valid_through: str | None = None  # ISO date the source says the post expires

"""Attributes of a job post (category "job")."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Level = Literal["intern", "fresher", "junior", "mid", "senior", "lead", "manager"]
WorkMode = Literal["onsite", "hybrid", "remote"]


class Comment(BaseModel):
    """A comment under a post (Facebook groups), as shown on the page."""

    model_config = ConfigDict(extra="ignore")

    author: str | None = None
    text: str
    posted_at: str | None = None  # ISO 8601


class JobAttributes(BaseModel):
    model_config = ConfigDict(extra="ignore")

    levels: list[Level] = Field(
        default_factory=list,
        description="Seniority levels in the title",
        json_schema_extra={"filter": "Level"},
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Canonical skill keys",
        json_schema_extra={"filter": "Skill"},
    )
    locations: list[str] = Field(
        default_factory=list,
        description="All canonical locations",
        json_schema_extra={"filter": "Location"},
    )
    work_mode: WorkMode | None = Field(None, json_schema_extra={"filter": "Work mode"})
    salary_negotiable: bool = False
    salary_text: str | None = Field(None, description="Salary as written by the source")
    employment_type: str | None = None  # e.g. FULL_TIME
    # Source's job category, e.g. "Backend Developer".
    expertise: str | None = Field(None, json_schema_extra={"filter": "Expertise"})
    experience_months: int | None = None
    valid_through: str | None = None  # ISO date the source says the post expires
    # Comments shown under the post (Facebook groups), at most 10.
    comments: list[Comment] = Field(default_factory=list, max_length=10)

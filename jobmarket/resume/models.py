"""Validated data models for a reusable master resume."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResumeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Contact(ResumeModel):
    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None


class ResumeBullet(ResumeModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    text: str = Field(min_length=1)
    tags: list[str] | None = None
    metrics: dict[str, str | int | float] | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_plain_bullet_text(cls, value: Any) -> Any:
        """Allow concise string bullets while still assigning each an ID."""
        if isinstance(value, str):
            return {"text": value}
        return value


class DateRange(ResumeModel):
    start: str
    end: str | None = None


class ExperienceEntry(ResumeModel):
    role: str
    company: str
    dates: DateRange
    bullets: list[ResumeBullet] = Field(default_factory=list)


class ProjectEntry(ResumeModel):
    name: str
    tech: list[str] = Field(default_factory=list)
    bullets: list[ResumeBullet] = Field(default_factory=list)


class EducationEntry(ResumeModel):
    institution: str
    degree: str
    field: str | None = None
    dates: DateRange | None = None
    details: list[str] = Field(default_factory=list)


class CertificationEntry(ResumeModel):
    name: str
    issuer: str | None = None
    date: str | None = None
    credential_url: str | None = None


class MasterResume(ResumeModel):
    contact: Contact
    summary: str = ""
    skills: dict[str, list[str]] = Field(default_factory=dict)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)

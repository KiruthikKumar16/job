"""Master-resume schema and JSON loading helpers."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .loader import ResumeLoadError, default_resume_path, load_master_resume, validate_master_resume
from .matcher import match_resume
from .models import (
    CertificationEntry,
    Contact,
    DateRange,
    EducationEntry,
    ExperienceEntry,
    MasterResume,
    ProjectEntry,
    ResumeBullet,
)
from .tailor import (
    OpenAICompatibleClient,
    TailoredBullet,
    TailoredOutput,
    TailoringError,
    tailor_resume,
    validate_tailored_output,
)

__all__ = [
    "CertificationEntry",
    "Contact",
    "DateRange",
    "EducationEntry",
    "ExperienceEntry",
    "MasterResume",
    "OpenAICompatibleClient",
    "ProjectEntry",
    "ResumeBullet",
    "render_resume_docx",
    "save_resume_docx",
    "TailoredBullet",
    "TailoredOutput",
    "TailoringError",
    "ResumeLoadError",
    "default_resume_path",
    "load_master_resume",
    "match_resume",
    "tailor_resume",
    "validate_master_resume",
    "validate_tailored_output",
]


def render_resume_docx(
    resume: MasterResume,
    *,
    match_result: Mapping[str, Any] | None = None,
    tailored_bullets: Mapping[str, str] | None = None,
) -> bytes:
    """Load the optional DOCX renderer only when a document is requested."""
    from .render import render_resume_docx as render

    return render(resume, match_result=match_result, tailored_bullets=tailored_bullets)


def save_resume_docx(data: bytes, path: str | Path) -> Path:
    """Load the DOCX writer only when a generated document is saved."""
    from .render import save_resume_docx as save

    return save(data, path)

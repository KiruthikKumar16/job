"""Load and validate a user's private master resume JSON file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import MasterResume


class ResumeLoadError(ValueError):
    """A readable file or schema error suitable for a CLI or UI message."""


def _format_validation_error(error: ValidationError) -> str:
    details = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "resume"
        details.append(f"{location}: {item['msg']}")
    return "; ".join(details)


def validate_master_resume(data: Any) -> MasterResume:
    """Validate a decoded mapping and return a typed master-resume model."""
    try:
        return MasterResume.model_validate(data)
    except ValidationError as error:
        raise ResumeLoadError(
            f"Resume data is invalid: {_format_validation_error(error)}"
        ) from error


def default_resume_path() -> Path:
    """Return the ignored personal-resume path at the repository root."""
    return Path(__file__).resolve().parents[2] / "resume" / "master_resume.json"


def load_master_resume(path: str | Path | None = None) -> MasterResume:
    """Read and validate master-resume JSON, with actionable file errors."""
    resume_path = Path(path) if path is not None else default_resume_path()
    try:
        raw = resume_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ResumeLoadError(
            f"Resume file not found: {resume_path}. Copy resume/master_resume.example.json "
            "to resume/master_resume.json and add your details."
        ) from error
    except OSError as error:
        raise ResumeLoadError(f"Could not read resume file '{resume_path}': {error}") from error
    except UnicodeError as error:
        raise ResumeLoadError(f"Resume file '{resume_path}' must be valid UTF-8 text.") from error

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ResumeLoadError(
            f"Resume file '{resume_path}' is not valid JSON (line {error.lineno}, column {error.colno}): {error.msg}."
        ) from error
    try:
        return validate_master_resume(data)
    except ResumeLoadError as error:
        detail = str(error).removeprefix("Resume data is invalid: ")
        raise ResumeLoadError(f"Resume file '{resume_path}' failed validation: {detail}") from error

"""Deterministic resume-to-job matching using normalized term overlap."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .models import MasterResume, ResumeBullet

_SYNONYMS = {
    "js": "javascript",
    "ecmascript": "javascript",
    "ts": "typescript",
    "postgres": "postgresql",
    "postgresql db": "postgresql",
    "amazon web services": "aws",
    "google cloud platform": "gcp",
    "k8s": "kubernetes",
    "nodejs": "node.js",
    "powerbi": "power bi",
    "ms sql": "sql server",
}
_TOKEN_PATTERN = re.compile(r"c\+\+|c#|node\.js|\.net|[a-z]+\d*|\d+(?:\.\d+)?", re.I)
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "the",
    "to",
    "with",
    "will",
    "you",
    "your",
    "experience",
    "experienced",
    "skill",
    "skills",
    "work",
    "working",
    "role",
    "team",
    "teams",
    "strong",
    "proficient",
    "knowledge",
}


def _as_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, set):
        return sorted(item.strip() for item in value if isinstance(item, str) and item.strip())
    if isinstance(value, (list, tuple)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _normalize_phrase(value: str) -> str:
    phrase = value.casefold().strip()
    phrase = re.sub(r"\s+", " ", phrase)
    for alias in sorted(_SYNONYMS, key=len, reverse=True):
        target = _SYNONYMS[alias]
        left_boundary = r"(?<![a-z0-9.])" if alias in {"js", "ts"} else r"(?<![a-z0-9])"
        phrase = re.sub(rf"{left_boundary}{re.escape(alias)}(?![a-z0-9])", target, phrase)
    return phrase


def _canonical_skill_set(resume: MasterResume) -> set[str]:
    values: list[str] = []
    for category_skills in resume.skills.values():
        values.extend(category_skills)
    for experience in resume.experience:
        for bullet in experience.bullets:
            values.extend(bullet.tags or [])
    for project in resume.projects:
        values.extend(project.tech)
        for bullet in project.bullets:
            values.extend(bullet.tags or [])
    return {_normalize_phrase(value) for value in values}


def _resume_text(resume: MasterResume) -> str:
    parts = [resume.summary]
    parts.extend(skill for values in resume.skills.values() for skill in values)
    for experience in resume.experience:
        parts.extend([experience.role, experience.company])
        parts.extend(bullet.text for bullet in experience.bullets)
        parts.extend(tag for bullet in experience.bullets for tag in (bullet.tags or []))
    for project in resume.projects:
        parts.extend([project.name, *project.tech])
        parts.extend(bullet.text for bullet in project.bullets)
        parts.extend(tag for bullet in project.bullets for tag in (bullet.tags or []))
    parts.extend(
        f"{item.degree} {item.field or ''} {item.institution}" for item in resume.education
    )
    parts.extend(f"{item.name} {item.issuer or ''}" for item in resume.certifications)
    return " ".join(part for part in parts if part)


def _candidate_terms(text: str) -> set[str]:
    normalized = _normalize_phrase(text)
    terms = set()
    for token in _TOKEN_PATTERN.findall(normalized):
        token = token.casefold()
        if token not in _STOP_WORDS:
            terms.add(token)
    return terms


def _skill_is_present(skill: str, canonical_skills: set[str], normalized_resume_text: str) -> bool:
    normalized = _normalize_phrase(skill)
    if normalized in canonical_skills:
        return True
    if normalized == "c":
        pattern = r"(?<!\w)c(?![\w+#])"
    elif normalized == "c++":
        pattern = r"(?<!\w)c\+\+(?![\w+#])"
    elif normalized == "c#":
        pattern = r"(?<!\w)c#(?![\w+#])"
    else:
        pattern = (
            r"(?<!\w)" + r"\s+".join(re.escape(part) for part in normalized.split()) + r"(?!\w)"
        )
    return re.search(pattern, normalized_resume_text, re.I) is not None


def _overlap_score(text: str, target_terms: set[str]) -> tuple[float, list[str]]:
    if not target_terms:
        return 0.0, []
    present = _candidate_terms(text)
    matched = sorted(target_terms & present)
    return len(matched) / len(target_terms), matched


def _bullet_record(
    bullet: ResumeBullet, *, title: str, text: str, extra: str = ""
) -> dict[str, Any]:
    return {
        "item_type": "bullet",
        "id": bullet.id,
        "title": title,
        "text": text,
        "tags": bullet.tags or [],
        "metrics": bullet.metrics or {},
        "_content": " ".join((text, " ".join(bullet.tags or []), extra)),
    }


def _recommendation_items(resume: MasterResume) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for experience in resume.experience:
        title = f"{experience.role} — {experience.company}"
        for bullet in experience.bullets:
            items.append(_bullet_record(bullet, title=title, text=bullet.text))
    for project in resume.projects:
        project_context = " ".join(project.tech + [bullet.text for bullet in project.bullets])
        project_skills = ", ".join(project.tech)
        items.append(
            {
                "item_type": "project",
                "id": None,
                "title": project.name,
                "text": " ".join(bullet.text for bullet in project.bullets),
                "tags": project.tech,
                "metrics": {},
                "_content": " ".join((project.name, project_context, project_skills)),
            }
        )
        for bullet in project.bullets:
            items.append(
                _bullet_record(
                    bullet,
                    title=project.name,
                    text=bullet.text,
                    extra=" ".join(project.tech),
                )
            )
    return items


def match_resume(
    resume: MasterResume,
    requirements: Mapping[str, Any] | str | None,
    top_n: int = 5,
) -> dict[str, Any]:
    """Score a resume and rank relevant project/bullet evidence without an LLM.

    Score weights are 70% required skills, 20% preferred skills, and 10% keyword
    coverage. Weights for empty categories are redistributed across categories
    that have targets, so a requirements object containing only keywords remains
    useful. Ranked items use keyword overlap and stable tie-breaks.
    """
    if not isinstance(resume, MasterResume):
        raise TypeError("resume must be a MasterResume instance")
    if top_n < 0:
        raise ValueError("top_n must be zero or greater")
    if requirements is None or requirements == "":
        requirement_data: Mapping[str, Any] = {}
    elif isinstance(requirements, str):
        try:
            parsed = json.loads(requirements)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"requirements must be a mapping or valid JSON: {error.msg}"
            ) from error
        if not isinstance(parsed, Mapping):
            raise ValueError("requirements JSON must contain an object")
        requirement_data = parsed
    elif isinstance(requirements, Mapping):
        requirement_data = requirements
    else:
        raise TypeError("requirements must be a mapping, JSON object string, or None")

    required = list(dict.fromkeys(_as_strings(requirement_data.get("required_skills"))))
    preferred = list(dict.fromkeys(_as_strings(requirement_data.get("preferred_skills"))))
    keywords = _as_strings(requirement_data.get("keywords"))
    target_terms = _candidate_terms(
        " ".join(required + preferred + keywords + _as_strings(requirement_data.get("tools")))
    )

    canonical_skills = _canonical_skill_set(resume)
    resume_text = _resume_text(resume)
    resume_terms = _candidate_terms(resume_text)
    normalized_resume_text = _normalize_phrase(resume_text)
    matched_required = [
        skill
        for skill in required
        if _skill_is_present(skill, canonical_skills, normalized_resume_text)
    ]
    matched_preferred = [
        skill
        for skill in preferred
        if _skill_is_present(skill, canonical_skills, normalized_resume_text)
    ]
    matched_skills = list(dict.fromkeys(matched_required + matched_preferred))
    missing_required = [skill for skill in required if skill not in matched_required]
    missing_preferred = [skill for skill in preferred if skill not in matched_preferred]

    categories = [
        (0.70, len(matched_required), len(required)),
        (0.20, len(matched_preferred), len(preferred)),
    ]
    keyword_targets = _candidate_terms(" ".join(keywords))
    categories.append((0.10, len(keyword_targets & resume_terms), len(keyword_targets)))
    active_weight = sum(weight for weight, _, total in categories if total)
    weighted_score = sum(weight * found / total for weight, found, total in categories if total)
    score = round(100 * weighted_score / active_weight) if active_weight else 0

    ranked = []
    for item in _recommendation_items(resume):
        overlap, matched_terms = _overlap_score(item["_content"], target_terms)
        if overlap <= 0:
            continue
        ranked.append(
            {
                key: value
                for key, value in {
                    **item,
                    "relevance_score": round(overlap * 100),
                    "matched_terms": matched_terms,
                }.items()
                if not key.startswith("_")
            }
        )
    ranked.sort(
        key=lambda item: (
            -item["relevance_score"],
            item["item_type"],
            item["title"].casefold(),
            item["id"] or "",
        )
    )
    return {
        "match_score": max(0, min(100, score)),
        "matched_skills": matched_skills,
        "missing_required_skills": missing_required,
        "missing_preferred_skills": missing_preferred,
        "top_items": ranked[:top_n],
    }

"""Title-aware NLP normalization for Indian job listings."""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

SKILL_CATALOG = ["Python", "Java", "JavaScript", "TypeScript", "React", "Node.js", "AWS", "GCP", "Azure", "Docker", "Kubernetes", "SQL", "PostgreSQL", "MongoDB", "Git", "FastAPI", "Flask", "Django", "Spark", "Kafka", "Terraform"]
TITLE_ONLY_MARKER = "Extracted from Title Only"
ENTRY_PATTERN = re.compile(r"\b(fresher|trainee|intern|associate|graduate|sde[- ]?1|junior|0[- ]?[12])\b", re.I)
MID_PATTERN = re.compile(r"\b(sde[- ]?2|l4|engineer\s+ii)\b", re.I)
SENIOR_PATTERN = re.compile(r"\b(sr\.?|senior|lead|principal|staff|manager|vp|avp|architect)\b", re.I)
DEGREE_PATTERNS = {
    "B.Tech": r"\b(b\.?tech|bachelor of technology)\b", "B.E.": r"\b(b\.?e\.?|bachelor of engineering)\b",
    "M.Tech": r"\b(m\.?tech|master of technology)\b", "M.E.": r"\b(m\.?e\.?|master of engineering)\b",
    "B.Sc": r"\b(b\.?sc)\b", "M.Sc": r"\b(m\.?sc)\b", "BCA": r"\b(bca)\b", "MCA": r"\b(mca)\b",
    "BS": r"\b(b\.?s\.?)\b", "MS": r"\b(m\.?s\.?)\b",
}
GENERIC_PATTERN = re.compile(r"\b(bachelor'?s?|master'?s?|degree|diploma)\b(?:\s+(?:in|of)\s+([A-Za-z\s]+))?", re.I)
FIELD_STOP_WORDS = re.compile(r"\b(?:required|preferred|optional|or|and|with|plus|years?|experience|degree|diploma)\b", re.I)


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _combined_text(text: str, title: str) -> str:
    return f"{_safe_text(title)} {_safe_text(text)}".strip()


def _field_of_study(content: str) -> str | None:
    """Normalize common technical fields even when generic wording is used."""
    lower = content.casefold()
    if re.search(r"\b(computer science|computer engineering|computing)\b", lower):
        return "Computer Science/IT"
    if re.search(r"\b(information technology|\bit\b)\b", lower):
        return "Information Technology"
    if re.search(r"\b(data science|artificial intelligence|machine learning)\b", lower):
        return "Data Science/AI"
    if re.search(r"\b(engineering)\b", lower):
        return "Engineering"
    return None


def extract_qualification(text: str, title: str) -> str:
    """Return a specific or generic degree label; every record gets a value."""
    content = _combined_text(text, title)
    field = _field_of_study(content)
    for label, pattern in DEGREE_PATTERNS.items():
        if re.search(pattern, content, re.I):
            return f"{label} ({field})" if field else label
    generic = GENERIC_PATTERN.search(content)
    if not generic:
        return "Degree Required"
    kind = generic.group(1).casefold()
    field_text = FIELD_STOP_WORDS.split((generic.group(2) or "").strip(), maxsplit=1)[0].strip(" ,.;:()-")
    generic_field = _field_of_study(field_text) or (field_text.title() if field_text else None)
    if kind.startswith("bachelor"):
        return f"Bachelor's ({generic_field or field or 'Any Field'})"
    if kind.startswith("master"):
        return f"Master's ({generic_field or field or 'Any Field'})"
    if kind == "diploma":
        return f"Diploma ({generic_field or field or 'Any Field'})"
    return f"Bachelor's ({generic_field or field})" if (generic_field or field) else "Degree Required"


def extract_skills(text: str, title: str) -> list[str]:
    description = _safe_text(text)
    content = _combined_text(description, title)
    skills = [skill for skill in SKILL_CATALOG if re.search(r"\b" + re.escape(skill) + r"\b", content, re.I)]
    return skills if skills or description.strip() else [TITLE_ONLY_MARKER]


def extract_experience_and_seniority(text: str, title: str) -> dict[str, float | str | None]:
    content = _safe_text(text)
    number = r"(\d{1,2}(?:\.\d+)?)"
    range_match = re.search(rf"\b{number}\s*(?:to|-|–)\s*{number}\s*(?:years?|yrs?)\b", content, re.I)
    single_match = re.search(rf"\b{number}\s*\+?\s*(?:years?|yrs?)\b", content, re.I)
    min_exp: float | None = None
    max_exp: float | None = None
    if range_match:
        min_exp, max_exp = float(range_match.group(1)), float(range_match.group(2))
    elif single_match:
        min_exp = float(single_match.group(1))
    safe_title = _safe_text(title)
    if SENIOR_PATTERN.search(safe_title) or (min_exp is not None and min_exp > 5):
        seniority = "Senior/Lead"
    elif MID_PATTERN.search(safe_title) or (min_exp is not None and 2 < min_exp <= 5):
        seniority = "Mid-Level"
    elif ENTRY_PATTERN.search(safe_title) or (min_exp is not None and min_exp <= 2):
        seniority = "Entry-Level"
    else:
        seniority = "Not Specified"
    return {"min_exp": min_exp, "max_exp": max_exp, "seniority": seniority}


def extract_work_mode(text: str) -> str:
    content = _safe_text(text)
    if re.search(r"\bhybrid\b", content, re.I): return "Hybrid"
    if re.search(r"\b(remote|work\s+from\s+home|wfh|telecommut)\b", content, re.I): return "Remote"
    if re.search(r"\b(on[ -]?site|in[ -]?office|office[- ]based)\b", content, re.I): return "On-site"
    return "Not Specified"


def enrich_jobs(df: pd.DataFrame) -> pd.DataFrame:
    """Return all input jobs enriched; no job is removed for missing NLP fields."""
    result = df.copy()
    descriptions = result.get("description", pd.Series("", index=result.index)).fillna("").astype(str)
    titles = result.get("title", pd.Series("", index=result.index)).fillna("").astype(str)
    result["extracted_skills"] = [extract_skills(text, title) for text, title in zip(descriptions, titles, strict=True)]
    result["qualification"] = [extract_qualification(text, title) for text, title in zip(descriptions, titles, strict=True)]
    experience = [extract_experience_and_seniority(text, title) for text, title in zip(descriptions, titles, strict=True)]
    result["min_exp"] = [item["min_exp"] for item in experience]
    result["max_exp"] = [item["max_exp"] for item in experience]
    result["seniority"] = [item["seniority"] for item in experience]
    result["work_mode"] = descriptions.map(extract_work_mode)
    result["description_missing"] = descriptions.str.strip().eq("")
    result["data_quality_score"] = result.apply(_data_quality_score, axis=1)
    return result


def _data_quality_score(row: pd.Series) -> int:
    """Score available fields without penalizing intentionally title-only records."""
    fields = ("title", "company", "location", "job_url", "qualification", "extracted_skills")
    present = sum(bool(row.get(field)) for field in fields)
    return round((present / len(fields)) * 100)

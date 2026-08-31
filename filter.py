"""Filtering helpers for enriched job records."""
from __future__ import annotations

import re

import pandas as pd


def filter_jobs(df: pd.DataFrame, max_exp: float | None = None, min_exp: float | None = None,
                seniority: str | None = None, required_skills: list[str] | None = None,
                degree: str | list[str] | None = None) -> pd.DataFrame:
    """Filter records without rejecting LinkedIn title-only job cards by default."""
    result = df.copy()
    descriptions = result.get("description", pd.Series("", index=result.index)).fillna("").astype(str)
    if required_skills:
        wanted = {skill.casefold() for skill in required_skills if skill.strip()}

        def skills_match(row: pd.Series) -> bool:
            content = f"{row.get('title', '')} {row.get('description', '')}"
            return all(re.search(r"\b" + re.escape(skill) + r"\b", content, re.I) for skill in wanted)

        result = result[result.apply(skills_match, axis=1)]
        descriptions = descriptions.loc[result.index]
    if degree:
        wanted_degrees = {item.casefold() for item in ([degree] if isinstance(degree, str) else degree)}
        qualifications = result.get("qualification", pd.Series("", index=result.index)).fillna("")
        normalized_qualifications = qualifications.astype(str).str.casefold()
        matches_degree = normalized_qualifications.isin(wanted_degrees) | normalized_qualifications.str.startswith(
            tuple(f"{item} (" for item in wanted_degrees)
        )
        result = result[matches_degree | descriptions.str.strip().eq("")]
    seniorities = result.get("seniority", pd.Series("Not Specified", index=result.index)).fillna("Not Specified")
    min_values = pd.to_numeric(result.get("min_exp", pd.Series(index=result.index)), errors="coerce")
    max_values = pd.to_numeric(result.get("max_exp", pd.Series(index=result.index)), errors="coerce")
    if max_exp is not None:
        result = result[(min_values <= max_exp) | seniorities.eq("Entry-Level")]
    if min_exp is not None:
        numeric_upper = max_values.fillna(min_values)
        result = result[(numeric_upper >= min_exp) | seniorities.eq("Senior/Lead")]
    if seniority:
        result = result[seniorities.eq(seniority)]
    return result.reset_index(drop=True)

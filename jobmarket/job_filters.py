"""Filtering helpers for enriched job records."""

from __future__ import annotations

import re

import pandas as pd


def filter_jobs(
    df: pd.DataFrame,
    max_exp: float | None = None,
    min_exp: float | None = None,
    seniority: str | None = None,
    required_skills: list[str] | None = None,
    degree: str | list[str] | None = None,
) -> pd.DataFrame:
    """Filter records using one aligned mask, retaining truly unclassified listings."""
    result = df.copy().reset_index(drop=True)
    mask = pd.Series(True, index=result.index, dtype=bool)
    descriptions = result.get("description", pd.Series("", index=result.index))
    descriptions = descriptions.fillna("").astype(str)

    if required_skills:
        wanted = [skill.casefold() for skill in required_skills if skill.strip()]

        def skills_match(row: pd.Series) -> bool:
            content = f"{row.get('title', '')} {row.get('description', '')}"
            return all(
                re.search(r"\b" + re.escape(skill) + r"\b", content, re.I) for skill in wanted
            )

        mask &= result.apply(skills_match, axis=1)

    if degree:
        wanted_degrees = {
            item.casefold() for item in ([degree] if isinstance(degree, str) else degree)
        }
        qualifications = result.get("qualification", pd.Series("", index=result.index))
        normalized_qualifications = qualifications.fillna("").astype(str).str.casefold()
        matches_degree = normalized_qualifications.isin(wanted_degrees) | (
            normalized_qualifications.str.startswith(tuple(f"{item} (" for item in wanted_degrees))
        )
        mask &= matches_degree | descriptions.str.strip().eq("")

    seniorities = result.get("seniority", pd.Series("Not Specified", index=result.index))
    seniorities = seniorities.fillna("Not Specified")
    min_values = pd.to_numeric(
        result.get("min_exp", pd.Series(index=result.index, dtype=float)), errors="coerce"
    )
    max_values = pd.to_numeric(
        result.get("max_exp", pd.Series(index=result.index, dtype=float)), errors="coerce"
    )
    unclassified = min_values.isna() & max_values.isna() & seniorities.eq("Not Specified")

    if max_exp is not None:
        mask &= (min_values <= max_exp) | seniorities.eq("Entry-Level") | unclassified
    if min_exp is not None and min_exp > 0:
        numeric_upper = max_values.fillna(min_values)
        mask &= (numeric_upper >= min_exp) | seniorities.eq("Senior/Lead") | unclassified
    if seniority:
        mask &= seniorities.eq(seniority)

    return result.loc[mask].reset_index(drop=True)

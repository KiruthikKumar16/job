"""Cross-source job listing deduplication."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

import pandas as pd


def normalize_job_field(value: Any) -> str:
    """Normalize a title, company, or location for stable comparison."""
    if value is None or pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    words = text.split()
    legal_suffixes = {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "company",
        "co",
    }
    while words and words[-1] in legal_suffixes:
        words.pop()
    if words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def job_dedup_bucket(title: Any, company: Any, location: Any) -> str:
    """Return the indexed candidate bucket used before fuzzy comparison."""
    normalized_title = normalize_job_field(title)
    normalized_company = normalize_job_field(company)
    normalized_location = normalize_job_field(location)
    if not normalized_title or not normalized_company or not normalized_location:
        return ""
    number_tokens = ",".join(re.findall(r"\d+", normalized_title))
    return "|".join(
        (normalized_company[:1], normalized_location[:1], normalized_title[:1], number_tokens)
    )


def _similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _source_values(row: pd.Series) -> list[str]:
    values: list[str] = []
    existing = row.get("sources")
    if isinstance(existing, str):
        try:
            import json

            decoded = json.loads(existing)
            existing = decoded if isinstance(decoded, list) else [existing]
        except (ValueError, TypeError):
            existing = [part.strip() for part in existing.split(",")]
    if isinstance(existing, (list, tuple, set)):
        values.extend(
            str(item).strip() for item in existing if item is not None and str(item).strip()
        )
    site = row.get("site")
    if site is not None and not pd.isna(site) and str(site).strip():
        values.append(str(site).strip())
    return list(dict.fromkeys(values))


def deduplicate_jobs(frame: pd.DataFrame, threshold: float = 0.92) -> pd.DataFrame:
    """Collapse fuzzy cross-source duplicates, preferring quality and merging sources.

    Similarity is a weighted ratio over normalized title (50%), company (30%),
    and location (20%). Every field must also be within 0.15 of the requested
    threshold. Rows without all three identity fields are kept independently.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if frame.empty:
        result = frame.copy()
        if "sources" not in result:
            result["sources"] = pd.Series(dtype=object)
        return result.reset_index(drop=True)

    result = frame.copy().reset_index(drop=True)
    normalized: list[tuple[str, str, str] | None] = []
    for _, row in result.iterrows():
        fields = (
            normalize_job_field(row.get("title")),
            normalize_job_field(row.get("company")),
            normalize_job_field(row.get("location")),
        )
        normalized.append(fields if all(fields) else None)

    exact_units: dict[tuple[str, str, str], int] = {}
    units: list[list[int]] = []
    unit_fields: list[tuple[str, str, str] | None] = []
    for index, row_identity in enumerate(normalized):
        if row_identity is not None and row_identity in exact_units:
            units[exact_units[row_identity]].append(index)
        else:
            unit_index = len(units)
            units.append([index])
            unit_fields.append(row_identity)
            if row_identity is not None:
                exact_units[row_identity] = unit_index

    parents = list(range(len(units)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    # Exact duplicates were grouped above. Fuzzy candidates share coarse
    # identity prefixes and the same numeric title tokens, which avoids O(n^2)
    # comparisons for large imports containing titles such as "Analyst 1204".
    blocks: dict[tuple[str, str, str, tuple[str, ...]], list[int]] = {}
    for index, unit_identity in enumerate(unit_fields):
        if unit_identity is None:
            continue
        title, company, location = unit_identity
        number_tokens = tuple(re.findall(r"\d+", title))
        block = (company[:1], location[:1], title[:1], number_tokens)
        for prior in blocks.setdefault(block, []):
            prior_fields: tuple[str, str, str] | None = unit_fields[prior]
            assert prior_fields is not None
            title_score = _similarity(title, prior_fields[0])
            company_score = _similarity(company, prior_fields[1])
            location_score = _similarity(location, prior_fields[2])
            weighted_score = title_score * 0.5 + company_score * 0.3 + location_score * 0.2
            if (
                min(title_score, company_score, location_score) >= threshold - 0.15
                and weighted_score >= threshold
            ):
                union(index, prior)
        blocks[block].append(index)

    clusters: dict[int, list[int]] = {}
    for unit_index, unit in enumerate(units):
        clusters.setdefault(find(unit_index), []).extend(unit)

    quality = pd.to_numeric(
        result.get("data_quality_score", pd.Series(0, index=result.index)), errors="coerce"
    ).fillna(0)
    kept_rows: list[pd.Series] = []
    for members in clusters.values():
        winner = max(members, key=lambda index: (float(quality.iloc[index]), -index))
        row = result.iloc[winner].copy()
        merged_sources: list[str] = []
        for index in members:
            for source in _source_values(result.iloc[index]):
                if source not in merged_sources:
                    merged_sources.append(source)
        row["sources"] = merged_sources
        kept_rows.append(row)

    return pd.DataFrame(kept_rows).reset_index(drop=True)

#!/usr/bin/env python3
"""Debug script to understand the scoring logic."""

import pandas as pd
from parser import _data_quality_score

def debug_scoring():
    """Debug the scoring logic step by step."""

    # Test case: core fields complete, enrichment fields partially complete
    test_data = pd.DataFrame([{
        "title": "Software Engineer",
        "company": "Tech Corp",
        "location": "New York",
        "job_url": "http://test.com/job1",
        "description": "Python Django experience required",
        "date_posted": "2026-01-01",
        # Enrichment fields: one present, one empty
        "qualification": "Bachelor's (Computer Science)",  # This should count as present
        "extracted_skills": [],  # This should count as not present (empty list)
    }])

    row = test_data.iloc[0]
    print("Row data:")
    for col in test_data.columns:
        val = row[col]
        print(f"  {col}: {repr(val)} -> bool: {bool(val)}")

    # Core fields
    core_fields = ("title", "company", "location", "job_url", "description", "date_posted")
    present_core = sum(bool(row.get(field)) for field in core_fields)
    print(f"\nCore fields present: {present_core}/{len(core_fields)}")
    for field in core_fields:
        val = row.get(field)
        print(f"  {field}: {repr(val)} -> {bool(val)}")

    # Enriched fields
    enriched_fields = ("qualification", "extracted_skills")
    present_enriched = sum(bool(row.get(field)) for field in enriched_fields)
    print(f"\nEnriched fields present: {present_enriched}/{len(enriched_fields)}")
    for field in enriched_fields:
        val = row.get(field)
        print(f"  {field}: {repr(val)} -> {bool(val)}")

    # Apply logic
    print(f"\nLogic:")
    print(f"  present_enriched > 0? {present_enriched > 0}")
    if present_enriched > 0:
        total_fields = len(core_fields) + len(enriched_fields)
        present = present_core + present_enriched
        print(f"  Using enriched logic: total={total_fields}, present={present}")
    else:
        total_fields = len(core_fields)
        present = present_core
        print(f"  Using core-only logic: total={total_fields}, present={present}")

    score = round((present / total_fields) * 100) if total_fields > 0 else 0
    print(f"  Final score: {score}%")

    # Compare with function
    func_score = _data_quality_score(row)
    print(f"  Function result: {func_score}%")
    assert score == func_score, f"Mismatch: {score} vs {func_score}"

if __name__ == "__main__":
    debug_scoring()
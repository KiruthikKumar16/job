import sys
import os
# Add the current directory to the path so we can import app and scraper
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

import pandas as pd
from app import _prepare_frame
from parser import _data_quality_score as _quality_score

def test_prepare_frame_applies_fallback_for_missing_quality_score():
    """Test that a row with missing data_quality_score gets the fallback computed."""
    # Create a minimal DataFrame with the necessary columns
    df = pd.DataFrame([{
        "title": "Test Job",
        "company": "Test Company",
        "location": "Test Location",
        "job_url": "http://test.com/job",
        "description": "Test description",
        "date_posted": None,
        "salary_min": None,
        "salary_max": None,
        "currency": "USD",
        # Note: data_quality_score column is intentionally omitted
        # These columns will be processed by _prepare_frame
        "qualification": "Degree Required",
        "extracted_skills": ["Python", "SQL"],
        "site": "indeed",
        "search_term": "test",
        "seniority": "Mid-Level",
        "work_mode": "Remote"
    }])

    print("=== INPUT DF ===")
    print("Columns:", list(df.columns))
    print("Has data_quality_score?", "data_quality_score" in df.columns)
    print(df)
    print()

    # Process the frame
    result = _prepare_frame(df)

    print("=== RESULT DF ===")
    print("Columns:", list(result.columns))
    print("Has data_quality_score?", "data_quality_score" in result.columns)
    print(result)
    print()

    # Calculate what the fallback score should be for this row
    expected_quality = _quality_score(result.iloc[0])
    print(f"Expected quality score (fallback): {expected_quality}")
    print(f"Actual quality score in result: {result.iloc[0]['data_quality_score']}")
    print(f"Equal? {result.iloc[0]['data_quality_score'] == expected_quality}")

    if result.iloc[0]["data_quality_score"] != expected_quality:
        print("\n=== DEBUGGING WHY THEY'RE DIFFERENT ===")
        row = result.iloc[0]

        # Let's manually compute what _data_quality_score sees
        core_fields = ("title", "company", "location", "job_url", "description", "date_posted")
        enriched_fields = ("qualification", "extracted_skills")

        print("Field values:")
        for field in core_fields:
            value = row.get(field)
            print(f"  {field}: {repr(value)} -> bool: {bool(value)}")

        for field in enriched_fields:
            value = row.get(field)
            print(f"  {field}: {repr(value)} -> bool: {bool(value)}")

        present_core = sum(bool(row.get(field)) for field in core_fields)
        present_enriched = sum(bool(row.get(field)) for field in enriched_fields)

        print(f"\nPresent core: {present_core}/{len(core_fields)}")
        print(f"Present enriched: {present_enriched}/{len(enriched_fields)}")

        if present_enriched > 0:
            total_fields = len(core_fields) + len(enriched_fields)
            present = present_core + present_enriched
        else:
            total_fields = len(core_fields)
            present = present_core

        print(f"Total fields: {total_fields}")
        print(f"Present: {present}")
        print(f"Computed score: {round((present / total_fields) * 100) if total_fields > 0 else 0}")

if __name__ == "__main__":
    test_prepare_frame_applies_fallback_for_missing_quality_score()
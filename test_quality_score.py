#!/usr/bin/env python3
"""Test script to verify the shared data quality score function works correctly."""

import pandas as pd
from parser import _data_quality_score

def test_raw_data_scoring():
    """Test scoring of raw scraped data (before enrichment)."""
    print("Testing raw data scoring...")

    # Raw data similar to what comes from scraper (before enrichment)
    raw_data = pd.DataFrame([{
        "site": "indeed",
        "title": "Software Engineer",
        "company": "Tech Corp",
        "location": "New York",
        "job_url": "http://test.com/job1",
        "description": "Python Django experience required",
        "date_posted": "2026-01-01",
        # These fields are not present in raw data
        "qualification": None,
        "extracted_skills": None,
    }])

    score = _data_quality_score(raw_data.iloc[0])
    print(f"Raw data score: {score}")
    # Should be 6/6 = 100% for core fields (qualification and extracted_skills are None/not present)
    assert score == 100, f"Expected 100, got {score}"

    # Test with missing fields
    raw_data_missing = pd.DataFrame([{
        "site": "indeed",
        "title": "Software Engineer",
        # missing company
        "location": "New York",
        "job_url": "http://test.com/job1",
        # missing description
        # missing date_posted
    }])

    score_missing = _data_quality_score(raw_data_missing.iloc[0])
    print(f"Raw data with missing fields score: {score_missing}")
    # Should be 3/6 = 50% for core fields present
    assert score_missing == 50, f"Expected 50, got {score_missing}"

def test_enriched_data_scoring():
    """Test scoring of enriched data (after parser.enrich_jobs)."""
    print("\nTesting enriched data scoring...")

    # Enriched data similar to what comes from parser.enrich_jobs
    enriched_data = pd.DataFrame([{
        "site": "indeed",
        "title": "Software Engineer",
        "company": "Tech Corp",
        "location": "New York",
        "job_url": "http://test.com/job1",
        "description": "Python Django experience required",
        "date_posted": "2026-01-01",
        # Enriched fields
        "qualification": "Bachelor's (Computer Science)",
        "extracted_skills": ["Python", "Django", "APIs"],
    }])

    score = _data_quality_score(enriched_data.iloc[0])
    print(f"Enriched data score: {score}")
    # Should be 8/8 = 100% (6 core + 2 enriched)
    assert score == 100, f"Expected 100, got {score}"

    # Test with missing enriched fields
    enriched_data_missing = pd.DataFrame([{
        "site": "indeed",
        "title": "Software Engineer",
        "company": "Tech Corp",
        "location": "New York",
        "job_url": "http://test.com/job1",
        "description": "Python Django experience required",
        "date_posted": "2026-01-01",
        # Missing enriched fields
        "qualification": "",  # empty string
        "extracted_skills": [],  # empty list
    }])

    score_missing = _data_quality_score(enriched_data_missing.iloc[0])
    print(f"Enriched data with missing enriched fields score: {score_missing}")
    # Should be 6/8 = 75% (6 core present, 2 enriched missing)
    assert score_missing == 75, f"Expected 75, got {score_missing}"

def test_edge_cases():
    """Test edge cases."""
    print("\nTesting edge cases...")

    # Completely empty row
    empty_row = pd.Series({})
    score = _data_quality_score(empty_row)
    print(f"Empty row score: {score}")
    assert score == 0, f"Expected 0, got {score}"

    # Row with all None values
    none_row = pd.Series({
        "title": None,
        "company": None,
        "location": None,
        "job_url": None,
        "description": None,
        "date_posted": None,
        "qualification": None,
        "extracted_skills": None,
    })
    score = _data_quality_score(none_row)
    print(f"All None row score: {score}")
    assert score == 0, f"Expected 0, got {score}"

if __name__ == "__main__":
    test_raw_data_scoring()
    test_enriched_data_scoring()
    test_edge_cases()
    print("\nAll tests passed! ✓")
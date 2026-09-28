import sys
import os
# Add the current directory to the path so we can import app and scraper
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

import pandas as pd
from app import _prepare_frame
from parser import _data_quality_score as _quality_score

def test_prepare_frame_preserves_zero_quality_score():
    """Test that a row with data_quality_score=0 keeps that value after _prepare_frame."""
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
        # Set data_quality_score to 0 explicitly
        "data_quality_score": 0,
        # These columns will be processed by _prepare_frame
        "qualification": "Degree Required",
        "extracted_skills": ["Python", "SQL"],
        "site": "indeed",
        "search_term": "test",
        "seniority": "Mid-Level",
        "work_mode": "Remote"
    }])

    # Process the frame
    result = _prepare_frame(df)

    # The data_quality_score should remain 0 (not be overwritten by fallback)
    assert result.iloc[0]["data_quality_score"] == 0, \
        f"Expected data_quality_score to remain 0, got {result.iloc[0]['data_quality_score']}"

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

    # Process the frame
    result = _prepare_frame(df)

    # Calculate what the fallback score should be for this row
    expected_quality = _quality_score(result.iloc[0])

    # The data_quality_score should equal the fallback score
    assert result.iloc[0]["data_quality_score"] == expected_quality, \
        f"Expected data_quality_score to be {expected_quality} (fallback), got {result.iloc[0]['data_quality_score']}"

def test_prepare_frame_handles_mixed_quality_scores():
    """Test handling of both zero scores and missing scores in the same DataFrame."""
    # Create a DataFrame with two rows
    df = pd.DataFrame([
        {
            "title": "Job 1",
            "company": "Company A",
            "location": "Location A",
            "job_url": "http://test.com/job1",
            "description": "Description 1",
            "date_posted": None,
            "salary_min": None,
            "salary_max": None,
            "currency": "USD",
            # Explicitly set to 0 - should remain 0
            "data_quality_score": 0,
            "qualification": "Degree Required",
            "extracted_skills": ["Python", "SQL"],
            "site": "indeed",
            "search_term": "test",
            "seniority": "Mid-Level",
            "work_mode": "Remote"
        },
        {
            "title": "Job 2",
            "company": "Company B",
            "location": "Location B",
            "job_url": "http://test.com/job2",
            "description": "Description 2",
            "date_posted": None,
            "salary_min": None,
            "salary_max": None,
            "currency": "USD",
            # data_quality_score missing - should get fallback
            "qualification": "Degree Required",
            "extracted_skills": ["Java", "Spring"],
            "site": "linkedin",
            "search_term": "test",
            "seniority": "Senior",
            "work_mode": "Office"
        }
    ])

    # Process the frame
    result = _prepare_frame(df)

    # First row should keep its 0 score
    assert result.iloc[0]["data_quality_score"] == 0, \
        f"Expected first row data_quality_score to remain 0, got {result.iloc[0]['data_quality_score']}"

    # Second row should get fallback score
    expected_quality_2 = _quality_score(result.iloc[1])
    assert result.iloc[1]["data_quality_score"] == expected_quality_2, \
        f"Expected second row data_quality_score to be {expected_quality_2} (fallback), got {result.iloc[1]['data_quality_score']}"

if __name__ == "__main__":
    test_prepare_frame_preserves_zero_quality_score()
    test_prepare_frame_applies_fallback_for_missing_quality_score()
    test_prepare_frame_handles_mixed_quality_scores()
    print("All tests passed!")
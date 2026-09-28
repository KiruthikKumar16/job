"""Tests for jobmarket.job_filters."""

import numpy as np
import pandas as pd
from jobmarket.job_filters import filter_jobs


def test_filter_exp_unclassified_kept():
    """Test that unclassified rows (min_exp/max_exp NaN and seniority Not Specified) are kept when exp filters are applied."""
    # Create a DataFrame with various rows
    df = pd.DataFrame(
        {
            "title": ["Job1", "Job2", "Job3", "Job4", "Job5"],
            "company": ["A", "B", "C", "D", "E"],
            "location": ["X", "Y", "Z", "W", "V"],
            "job_url": ["url1", "url2", "url3", "url4", "url5"],
            "description": ["desc"] * 5,
            "date_posted": pd.NaT,
            "salary_min": [np.nan] * 5,
            "salary_max": [np.nan] * 5,
            "currency": [""] * 5,
            "min_exp": [
                np.nan,
                np.nan,
                2.0,
                np.nan,
                np.nan,
            ],  # Job1, Job2, Job4, Job5 are NaN; Job3 has 2.0
            "max_exp": [np.nan, np.nan, 4.0, np.nan, np.nan],  # same
            "seniority": [
                "Not Specified",
                "Entry-Level",
                "Mid-Level",
                "Not Specified",
                "Senior/Lead",
            ],
            # Note: We'll set the unclassified condition: min_exp and max_exp both NaN and seniority Not Specified
            # So Job1 and Job4 are unclassified (both NaN and Not Specified)
            # Job2: NaN but Entry-Level -> should be kept by max_exp exception (Entry-Level) and min_exp exception?
            # Job3: 2.0-4.0, Mid-Level -> should be kept by numeric if within range
            # Job5: NaN but Senior/Lead -> should be kept by min_exp exception (Senior/Lead) and max_exp?
        }
    )

    # Apply max_exp=3.0
    # Expected:
    #   Job1: unclassified -> kept
    #   Job2: Entry-Level -> kept (by max_exp exception)
    #   Job3: min_exp=2.0 <= 3.0 -> kept (numeric)
    #   Job4: unclassified -> kept
    #   Job5: Senior/Lead -> not Entry-Level, and min_exp NaN -> fails max_exp condition?
    #          Actually, for max_exp: condition is (min_values <= max_exp) | seniorities.eq("Entry-Level") | unclassified
    #          min_values for Job5 is NaN -> NaN <= 3.0 is False
    #          seniority is Senior/Level -> not Entry-Level -> False
    #          unclassified? min_exp and max_exp are NaN but seniority is Senior/Lead -> not Not Specified -> False
    #          So Job5 would be dropped by max_exp filter.
    #   However, note: we are also applying min_exp filter? Not in this test, only max_exp.
    #   So after max_exp=3.0, we expect Job1, Job2, Job3, Job4 to remain.

    result = filter_jobs(df, max_exp=3.0)
    assert len(result) == 4
    assert set(result["title"]) == {"Job1", "Job2", "Job3", "Job4"}

    # Apply min_exp=3.0
    # Expected:
    #   Job1: unclassified -> kept
    #   Job2: Entry-Level -> not Senior/Lead, and max_exp NaN -> fails min_exp condition?
    #          For min_exp: condition is (numeric_upper >= min_exp) | seniorities.eq("Senior/Lead") | unclassified
    #          numeric_upper = max_values.fillna(min_values) -> for Job2: both NaN -> NaN
    #          NaN >= 3.0 -> False
    #          seniority: Entry-Level -> not Senior/Lead -> False
    #          unclassified? min_exp and max_exp are NaN but seniority is Entry-Level -> not Not Specified -> False
    #          So Job2 would be dropped by min_exp filter.
    #   Job3: max_exp=4.0 >= 3.0 -> kept (numeric)
    #   Job4: unclassified -> kept
    #   Job5: Senior/Lead -> kept (by min_exp exception)
    #   So we expect Job1, Job3, Job4, Job5.

    result = filter_jobs(df, min_exp=3.0)
    assert len(result) == 4
    assert set(result["title"]) == {"Job1", "Job3", "Job4", "Job5"}

    # Apply both max_exp=3.0 and min_exp=1.0
    #   max_exp=3.0: keeps Job1, Job2, Job3, Job4 (as above)
    #   min_exp=1.0:
    #        Job1: unclassified -> kept
    #        Job2:
    #             numeric_upper = NaN -> 1.0 condition fails
    #             seniority: Entry-Level -> not Senior/Lead -> fails
    #             unclassified? no (because seniority is Entry-Level) -> fails -> dropped
    #        Job3:
    #             numeric_upper = 4.0 (from max_exp) -> 4.0 >= 1.0 -> True -> kept
    #        Job4: unclassified -> kept
    #        Job5:
    #             max_exp=3.0 filter:
    #                 min_values NaN -> False
    #                 seniority: Senior/Lead -> not Entry-Level -> False
    #                 unclassified? no (seniority Senior/Lead) -> False -> dropped by max_exp
    #   So after both filters, we expect Job1, Job3, Job4.

    result = filter_jobs(df, min_exp=1.0, max_exp=3.0)
    assert len(result) == 3
    assert set(result["title"]) == {"Job1", "Job3", "Job4"}

    # Test that if we set seniority filter, it still works
    result = filter_jobs(df, max_exp=3.0, seniority="Entry-Level")
    # Should only keep Job2 (because seniority filter overrides? Actually, the seniority filter is applied after exp filters)
    # But note: the seniority filter is applied as: result = result[seniorities.eq(seniority)]
    # So after max_exp filter we have Job1, Job2, Job3, Job4, then we filter by seniority=='Entry-Level' -> only Job2
    assert len(result) == 1
    assert result.iloc[0]["title"] == "Job2"


if __name__ == "__main__":
    test_filter_exp_unclassified_kept()
    print("All tests passed.")

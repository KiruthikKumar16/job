import os
import sys
from unittest.mock import patch

import pandas as pd

# Add the current directory to the path so we can import scraper
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from jobmarket.scraper import _extract_browser_cards, _normalise_jobspy, fetch_jobs


def test_normalise_jobspy_handles_various_column_names():
    """Test that _normalise_jobspy correctly maps JobSpy's version-dependent column names."""

    # Test case 1: Different column name variations
    test_cases = [
        # (input_data, expected_output_keys)
        (
            # Case with 'job_title' instead of 'title'
            [
                {
                    "site": "indeed",
                    "job_title": "Software Engineer",
                    "company_name": "Test Corp",
                    "job_location": "New York",
                    "job_url": "http://test.com",
                    "job_description": "Desc",
                    "date": "2026-01-01",
                    "min_amount": 100000,
                    "max_amount": 150000,
                    "salary_currency": "USD",
                }
            ],
            {
                "site": "indeed",
                "title": "Software Engineer",
                "company": "Test Corp",
                "location": "New York",
                "job_url": "http://test.com",
                "description": "Desc",
                "date_posted": "2026-01-01",
                "salary_min": 100000,
                "salary_max": 150000,
                "currency": "USD",
            },
        ),
        (
            # Case with 'title' and 'company' (no _name suffix)
            [
                {
                    "site": "linkedin",
                    "title": "Data Analyst",
                    "company": "Analytics Inc",
                    "location": "San Francisco",
                    "url": "http://test.com/job",
                    "job_description": "Analyze data",
                    "posted_date": "2026-02-01",
                    "min_salary": 80000,
                    "max_salary": 120000,
                    "salary_currency": "USD",
                }
            ],
            {
                "site": "linkedin",
                "title": "Data Analyst",
                "company": "Analytics Inc",
                "location": "San Francisco",
                "job_url": "http://test.com/job",
                "description": "Analyze data",
                "date_posted": "2026-02-01",
                "salary_min": 80000,
                "salary_max": 120000,
                "currency": "USD",
            },
        ),
        (
            # Case with missing optional fields (should become None/empty)
            [
                {
                    "site": "glassdoor",
                    "title": "Product Manager",
                    "company": "Product Co",
                    "location": "Chicago",
                    "job_url": "http://test.com/pm",
                }
            ],
            {
                "site": "glassdoor",
                "title": "Product Manager",
                "company": "Product Co",
                "location": "Chicago",
                "job_url": "http://test.com/pm",
                "description": "",
                "date_posted": None,
                "salary_min": None,
                "salary_max": None,
                "currency": "",
            },
        ),
    ]

    for i, (input_data, expected) in enumerate(test_cases):
        df_input = pd.DataFrame(input_data)
        site = input_data[0]["site"]
        result_df = _normalise_jobspy(df_input, site)

        # Convert first row to dict for comparison
        result_dict = result_df.iloc[0].to_dict()

        # Check each expected field
        for key, expected_value in expected.items():
            actual_value = result_dict.get(key)

            # Handle None/NaN comparisons
            if pd.isna(expected_value) and (
                actual_value is None or (isinstance(actual_value, float) and pd.isna(actual_value))
            ):
                continue
            elif actual_value == expected_value:
                continue
            else:
                raise AssertionError(
                    f"Test case {i + 1} failed: column '{key}' expected {repr(expected_value)}, got {repr(actual_value)}. "
                    f"Full result: {result_dict}"
                )


def test_extract_browser_cards_handles_different_platforms():
    """Test that _extract_browser_cards correctly extracts job cards from different platforms' HTML."""

    # Sample HTML snippets for different platforms (minimal but realistic)
    # Using class names that contain "location" in lowercase to match the [class*=location] selector
    # And appropriate selectors for company: [class*=company] or [data-testid*=employer]
    html_samples = {
        "indeed": """
        <div class="job_seen_beacon">
            <h2 class="jobTitle">Senior Python Developer</h2>
            <div class="companyName">Tech Solutions Inc</div>
            <div class="job-location">Bangalore, India</div>
            <a href="/viewjob?jk=123abc" class="jobtitle">Apply Now</a>
        </div>
        """,
        "linkedin": """
        <li data-occludable-job-id="456xyz">
            <div class="base-card">
                <h3 class="base-search-card__title">Frontend Engineer</h3>
                <h4 class="base-search-card__subtitle" data-testid="employer-name">WebStart Technologies</h4>
                <div class="job-location">Remote • Full-time</div>
                <a class="base-card__full-link" href="https://linkedin.com/jobs/view/456xyz">View Job</a>
            </div>
        </li>
        """,
        "naukri": """
        <article class="jobTuple bgWhite br4 mb-8">
            <div class="tuple">
                <div class="jobTupleHeader">
                    <h2><a class="title" href="https://www.naukri.com/job listings-java-developer-abc">Java Developer</a></h2>
                    <h3 class="subTitle"><a class="subTitle ellipsis fleft" data-testid="employer">Innovatech Solutions</a></h3>
                </div>
                <div class="jobTupleFooter">
                    <div class="location-details"><span>Pune, Maharashtra</span></div>
                </div>
            </div>
        </article>
        """,
        "glassdoor": """
        <li class="react-job-listing" data-testid="jobListing">
            <div class="jobContainer">
                <div class="jobInfoItem jobTitleContainer">
                    <a class="jobLink" href="https://www.glassdoor.com/job-listing/data-scientist-def">Data Scientist</a>
                </div>
                <div class="jobInfoItem" data-testid="employer">DataInsights Ltd</div>
                <div class="jobInfoItem location">Hyderabad, Telangana</div>
            </div>
        </li>
        """,
        "zip_recruiter": """
        <div class="job_result job-card">
            <h2 class="job_title"><a href="https://www.ziprecruiter.com/candidate/view/devops-engineer-789">DevOps Engineer</a></h2>
            <p class="company_name">CloudSystems Corp</p>
            <p class="job-location">Chennai, Tamil Nadu</p>
        </div>
        """,
    }

    expected_results = {
        "indeed": {
            "title": "Senior Python Developer",
            "company": "Tech Solutions Inc",
            "location": "Bangalore, India",
            "job_url": "https://www.indeed.com/viewjob?jk=123abc",
        },
        "linkedin": {
            "title": "Frontend Engineer",
            "company": "WebStart Technologies",
            "location": "Remote • Full-time",
            "job_url": "https://linkedin.com/jobs/view/456xyz",
        },
        "naukri": {
            "title": "Java Developer",
            "company": "Innovatech Solutions",
            "location": "Pune, Maharashtra",
            "job_url": "https://www.naukri.com/job listings-java-developer-abc",
        },
        "glassdoor": {
            "title": "Data Scientist",
            "company": "DataInsights Ltd",
            "location": "Hyderabad, Telangana",
            "job_url": "https://www.glassdoor.com/job-listing/data-scientist-def",
        },
        "zip_recruiter": {
            "title": "DevOps Engineer",
            "company": "CloudSystems Corp",
            "location": "Chennai, Tamil Nadu",
            "job_url": "https://www.ziprecruiter.com/candidate/view/devops-engineer-789",
        },
    }

    for site, html in html_samples.items():
        result_df = _extract_browser_cards(html, site, "Unknown Location", limit=1)

        assert not result_df.empty, f"No cards extracted for {site}"

        actual = result_df.iloc[0].to_dict()
        expected = expected_results[site]

        # Check key fields
        assert actual["site"] == site
        assert actual["title"] == expected["title"], f"Title mismatch for {site}"
        assert actual["company"] == expected["company"], (
            f"Company mismatch for {site}: expected {expected['company']}, got {actual['company']}"
        )
        assert actual["location"] == expected["location"], (
            f"Location mismatch for {site}: expected {expected['location']}, got {actual['location']}"
        )
        assert actual["job_url"] == expected["job_url"], f"Job URL mismatch for {site}"

        # Check that description is None (as browser cards don't have full description)
        assert actual["description"] is None, (
            f"Description should be None for browser cards in {site}"
        )

        # Check that card_summary contains the extracted text
        assert actual["card_summary"] is not None and len(actual["card_summary"]) > 0, (
            f"Card summary should be extracted for {site}"
        )


def test_fetch_jobs_deduplication_and_error_handling():
    """Test fetch_jobs deduplication and error handling branches."""

    # Mock data with duplicates
    mock_jobs_df = pd.DataFrame(
        [
            {
                "site": "indeed",
                "title": "Senior Python Developer",
                "company": "Tech Solutions Inc",
                "location": "Bangalore, India",
                "job_url": "http://test.com/job123",
                "description": "Python Django experience required",
                "date_posted": None,
                "salary_min": None,
                "salary_max": None,
                "currency": "",
            }
        ]
    )

    # Test 1: Deduplication - same job_url should appear only once
    call_count = 0

    def mock_jobspy_fetch_duplicates(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Return the same job twice on first call, once on second (to simulate duplicate across pages)
        if call_count == 1:
            return pd.concat([mock_jobs_df, mock_jobs_df], ignore_index=True)
        return mock_jobs_df.iloc[0:0]  # Empty DataFrame on subsequent calls

    def mock_playwright_fetch(*args, **kwargs):
        return pd.DataFrame()  # Empty

    with (
        patch("jobmarket.scraper._jobspy_fetch", side_effect=mock_jobspy_fetch_duplicates),
        patch("jobmarket.scraper._playwright_fetch", side_effect=mock_playwright_fetch),
        patch("jobmarket.scraper._naukri_fetch") as mock_naukri,
        patch("jobmarket.scraper._is_block_error", return_value=False),
    ):
        mock_naukri.return_value = pd.DataFrame()

        result = fetch_jobs(
            search_terms=["python"],
            locations=["bangalore"],
            platforms=["indeed"],
            max_results=10,
            proxies=None,
            country="India",
        )

        # Should have exactly one record despite duplicates being fed in
        assert len(result) == 1, f"Expected 1 record after deduplication, got {len(result)}"
        assert result.iloc[0]["job_url"] == "http://test.com/job123"

    # Test 2: Error handling - when all fetch methods fail, should return empty DataFrame
    def mock_jobspy_fetch_fail(*args, **kwargs):
        raise Exception("Network error")

    def mock_playwright_fetch_fail(*args, **kwargs):
        raise Exception("Browser failed")

    with (
        patch("jobmarket.scraper._jobspy_fetch", side_effect=mock_jobspy_fetch_fail),
        patch("jobmarket.scraper._playwright_fetch", side_effect=mock_playwright_fetch_fail),
        patch("jobmarket.scraper._naukri_fetch", side_effect=mock_jobspy_fetch_fail),
        patch("jobmarket.scraper._is_block_error", return_value=True),
    ):
        result = fetch_jobs(
            search_terms=["test"],
            locations=["test"],
            platforms=["indeed"],
            max_results=10,
            proxies=None,
            country="India",
        )

        # Should return empty DataFrame when all sources fail
        assert result.empty, "Should return empty DataFrame when all fetch methods fail"


def test_fetch_jobs_annotates_proxy_and_playwright_results():
    """Test that fetch_jobs properly annotates jobs from proxy retry and Playwright fallback paths."""

    # Mock data to return from fetch methods
    mock_jobs_df = pd.DataFrame(
        [
            {
                "site": "indeed",
                "title": "Test Job",
                "company": "Test Company",
                "location": "Test Location",
                "job_url": "http://test.com/job",
                "description": "Test description",
                "date_posted": None,
                "salary_min": None,
                "salary_max": None,
                "currency": "",
            }
        ]
    )

    # Mock _jobspy_fetch to raise a block error on first call (to trigger proxy retry)
    # and return jobs on second call (proxy retry success)
    jobspy_call_count = 0

    def mock_jobspy_fetch(*args, **kwargs):
        nonlocal jobspy_call_count
        jobspy_call_count += 1
        if jobspy_call_count == 1:
            # First call raises a block error to trigger proxy retry
            raise Exception("429 Too Many Requests")
        # Second call returns successful results
        return mock_jobs_df.copy()

    # Mock _playwright_fetch to return jobs (for when proxy retry also fails or isn't tried)
    def mock_playwright_fetch(*args, **kwargs):
        return mock_jobs_df.copy()

    # Mock get_proxy_pool to return some proxies for the retry attempt
    def mock_get_proxy_pool():
        return ["proxy1:8080", "proxy2:8080"]

    with (
        patch("jobmarket.scraper._jobspy_fetch", side_effect=mock_jobspy_fetch),
        patch("jobmarket.scraper._playwright_fetch", side_effect=mock_playwright_fetch),
        patch("jobmarket.proxy_manager.get_proxy_pool", side_effect=mock_get_proxy_pool),
        patch("jobmarket.scraper._is_block_error", return_value=True),
    ):  # Make sure our error is treated as a block error
        # Call fetch_jobs
        result = fetch_jobs(
            search_terms=["test"],
            locations=["test location"],
            platforms=["indeed"],
            max_results=10,
            proxies=["user:pass@proxy1:8080"],  # Initial proxies
            country="India",
        )

        # Assert we got results
        assert not result.empty, "Should have returned job results"

        # Assert that the metadata columns are properly annotated (not NaN/null)
        assert "search_term" in result.columns
        assert "search_location" in result.columns
        assert "scraped_at" in result.columns

        # Most importantly, assert that search_term and search_location are not null/NaN
        assert result["search_term"].notna().all(), "All rows should have search_term populated"
        assert result["search_location"].notna().all(), (
            "All rows should have search_location populated"
        )
        assert result["scraped_at"].notna().all(), "All rows should have scraped_at populated"

        # Assert the values are correct
        assert (result["search_term"] == "test").all(), "search_term should be 'test'"
        assert (result["search_location"] == "test location").all(), (
            "search_location should be 'test location'"
        )


def test_fetch_jobs_annotates_playwright_directly():
    """Test that fetch_jobs properly annotates jobs when Playwright is used directly (no proxy retry)."""

    # Mock data to return from fetch methods
    mock_jobs_df = pd.DataFrame(
        [
            {
                "site": "indeed",
                "title": "Test Job",
                "company": "Test Company",
                "location": "Test Location",
                "job_url": "http://test.com/job",
                "description": "Test description",
                "date_posted": None,
                "salary_min": None,
                "salary_max": None,
                "currency": "",
            }
        ]
    )

    # Mock _jobspy_fetch to raise a block error on first call
    # Mock get_proxy_pool to return empty list (no proxies available) so it goes straight to Playwright
    def mock_jobspy_fetch(*args, **kwargs):
        # Always raise a block error to trigger Playwright fallback
        raise Exception("403 Access Denied")

    # Mock _playwright_fetch to return jobs
    def mock_playwright_fetch(*args, **kwargs):
        return mock_jobs_df.copy()

    # Mock get_proxy_pool to return empty list (no working proxies)
    def mock_get_proxy_pool():
        return []

    with (
        patch("jobmarket.scraper._jobspy_fetch", side_effect=mock_jobspy_fetch),
        patch("jobmarket.scraper._playwright_fetch", side_effect=mock_playwright_fetch),
        patch("jobmarket.proxy_manager.get_proxy_pool", side_effect=mock_get_proxy_pool),
        patch("jobmarket.scraper._is_block_error", return_value=True),
    ):  # Make sure our error is treated as a block error
        # Call fetch_jobs WITHOUT initial proxies (so proxy retry won't be attempted)
        result = fetch_jobs(
            search_terms=["test"],
            locations=["test location"],
            platforms=["indeed"],
            max_results=10,
            proxies=None,  # No initial proxies
            country="India",
        )

        # Assert we got results
        assert not result.empty, "Should have returned job results"

        # Assert that the metadata columns are properly annotated (not NaN/null)
        assert "search_term" in result.columns
        assert "search_location" in result.columns
        assert "scraped_at" in result.columns

        # Most importantly, assert that search_term and search_location are not null/NaN
        assert result["search_term"].notna().all(), "All rows should have search_term populated"
        assert result["search_location"].notna().all(), (
            "All rows should have search_location populated"
        )
        assert result["scraped_at"].notna().all(), "All rows should have scraped_at populated"

        # Assert the values are correct
        assert (result["search_term"] == "test").all(), "search_term should be 'test'"
        assert (result["search_location"] == "test location").all(), (
            "search_location should be 'test location'"
        )


if __name__ == "__main__":
    test_normalise_jobspy_handles_various_column_names()
    test_extract_browser_cards_handles_different_platforms()
    test_fetch_jobs_deduplication_and_error_handling()
    test_fetch_jobs_annotates_proxy_and_playwright_results()
    test_fetch_jobs_annotates_playwright_directly()
    print("All tests passed!")

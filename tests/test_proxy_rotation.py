import sys
import os
# Add the current directory to the path so we can import app and scraper
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from unittest.mock import patch
import pandas as pd
from app import run_extraction

def test_proxy_rotation_in_run_extraction():
    """Test that run_extraction rotates proxies across multiple fetch_jobs calls."""

    # Mock data to return from fetch_jobs
    mock_jobs_df = pd.DataFrame([{
        "site": "indeed",
        "title": "Test Job",
        "company": "Test Company",
        "location": "Test Location",
        "job_url": "http://test.com/job",
        "description": "Test description",
        "date_posted": None,
        "salary_min": None,
        "salary_max": None,
        "currency": ""
    }])

    # Track which proxies were used in each call to _jobspy_fetch
    proxy_usage = []

    def mock_jobspy_fetch(term, location, site, max_results, proxies, country, hours_old=None):
        """Mock _jobspy_fetch that records which proxy was used."""
        # For our test, we'll pass a single proxy string (not a list) since run_extraction now passes proxy parameter
        proxy_usage.append(proxies)  # This will be the proxy string passed via the proxy parameter
        return mock_jobs_df.copy()

    # Test with 3 different proxies and 4 tasks (should rotate: 0,1,2,0)
    test_proxies = ["proxy1:8080", "proxy2:8080", "proxy3:8080"]
    test_terms = ["Data Analyst", "Data Engineer"]  # 2 terms
    test_locations = ["Bengaluru", "Hyderabad"]     # 2 locations
    test_platforms = ["indeed"]                     # 1 platform
    # Total combinations: 2 * 2 * 1 = 4 tasks

    with patch('scraper._jobspy_fetch', side_effect=mock_jobspy_fetch), \
         patch('scraper._naukri_fetch') as mock_naukri, \
         patch('scraper._playwright_fetch') as mock_playwright, \
         patch('scraper._is_block_error', return_value=False), \
         patch('scraper._normalise_user_proxies', return_value=test_proxies):

        mock_naukri.return_value = pd.DataFrame()
        mock_playwright.return_value = pd.DataFrame()

        # Run extraction with proxy rotation
        result, raw_count, valid_count, dropped_count, logs = run_extraction(
            terms=test_terms,
            locations=test_locations,
            platforms=test_platforms,
            max_results=10,
            min_exp=0.0,
            max_exp=10.0,
            proxies=test_proxies,  # These will be normalized by _normalise_user_proxies
            hours_old=None
        )
        # Since our mock data has job_url, we expect no dropped rows
        assert dropped_count == 0, f"Expected 0 dropped rows, got {dropped_count}"

        # Verify we got results (at least one should succeed due to our mocks)
        assert raw_count >= 1, f"Expected at least 1 raw record, got {raw_count}"

        # Verify that proxy rotation happened correctly
        # With 4 tasks and 3 proxies, we should see: proxy1, proxy2, proxy3, proxy1
        # Extract just the proxy strings from the lists
        actual_proxy_strings = [p[0] for p in proxy_usage]  # Each item is a list with one proxy
        expected_proxies = ["proxy1:8080", "proxy2:8080", "proxy3:8080", "proxy1:8080"]
        assert actual_proxy_strings == expected_proxies, f"Expected proxy usage {expected_proxies}, got {actual_proxy_strings}"

        print(f"Proxy usage across 4 tasks: {actual_proxy_strings}")
        print("Test passed!")

if __name__ == "__main__":
    test_proxy_rotation_in_run_extraction()
import sys
import os
# Add the current directory to the path so we can import app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

import tempfile
import os
from pathlib import Path
import pandas as pd
from app import load_jobs

def test_load_jobs_fallback_to_export():
    """Test that load_jobs falls back to job_market_export files when jobs.db doesn't exist."""
    # Create a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create a fake jobs export file with the correct naming pattern
        export_file = temp_path / "job_market_export_20260922T120000Z.csv"
        test_data = pd.DataFrame({
            "site": ["testsite"],
            "title": ["Test Job"],
            "company": ["Test Company"],
            "location": ["Test Location"],
            "job_url": ["http://test.com/job"],
            "description": ["Test description"],
            "date_posted": [None],
            "salary_min": [None],
            "salary_max": [None],
            "currency": ["USD"]
        })
        test_data.to_csv(export_file, index=False)

        # Ensure no jobs.db exists
        db_path = temp_path / "jobs.db"
        assert not db_path.exists()

        # Call load_jobs - it should find and load our export file
        result = load_jobs(str(db_path), str(temp_path))

        # Assertions
        assert not result.empty, "Should have loaded job data from export file"
        assert len(result) == 1, "Should have exactly one row"
        assert result.iloc[0]["title"] == "Test Job"
        assert result.iloc[0]["company"] == "Test Company"
        assert result.iloc[0]["location"] == "Test Location"

if __name__ == "__main__":
    test_load_jobs_fallback_to_export()
    print("Test passed!")
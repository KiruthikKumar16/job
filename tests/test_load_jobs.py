import tempfile
from pathlib import Path

import pandas as pd
from jobmarket import app
from jobmarket.app import load_jobs


def test_load_jobs_fallback_to_export():
    """Test that load_jobs falls back to job_market_export files when jobs.db doesn't exist."""
    # Create a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create a fake jobs export file with the correct naming pattern
        export_file = temp_path / "job_market_export_20260922T120000Z.csv"
        test_data = pd.DataFrame(
            {
                "site": ["testsite"],
                "title": ["Test Job"],
                "company": ["Test Company"],
                "location": ["Test Location"],
                "job_url": ["http://test.com/job"],
                "description": ["Test description"],
                "date_posted": [None],
                "salary_min": [None],
                "salary_max": [None],
                "currency": ["USD"],
            }
        )
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


def test_dashboard_cache_version_changes_when_database_changes(tmp_path):
    database = tmp_path / "jobs.db"
    first_version = app._dashboard_cache_version(str(database), str(tmp_path))
    database.write_text("new database content", encoding="utf-8")
    second_version = app._dashboard_cache_version(str(database), str(tmp_path))
    assert first_version != second_version


def test_dashboard_cache_invalidation_clears_database_and_csv_caches(monkeypatch):
    calls = []

    class CacheStub:
        def __init__(self, label):
            self.label = label

        def clear(self):
            calls.append(self.label)

    monkeypatch.setattr(app, "load_jobs", CacheStub("jobs"))
    monkeypatch.setattr(app, "load_csv_file", CacheStub("csv"))
    app.invalidate_dashboard_cache()
    assert calls == ["jobs", "csv"]


if __name__ == "__main__":
    test_load_jobs_fallback_to_export()
    print("Test passed!")

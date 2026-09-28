import pandas as pd
import pytest

pytest.importorskip("streamlit")

from jobmarket import app


def test_run_summary_records_failed_platform_while_retaining_other_results(monkeypatch, tmp_path):
    summaries = []

    def fetch_one(terms, locations, platforms, **_kwargs):
        platform = platforms[0]
        key = f"{platform}:{locations[0]}:{terms[0]}"
        if platform == "indeed":
            frame = pd.DataFrame(
                columns=["site", "title", "company", "location", "job_url", "description"]
            )
            frame.attrs["platform_status"] = {key: "failed: 403; browser fallback returned no jobs"}
            return frame
        frame = pd.DataFrame(
            [
                {
                    "site": platform,
                    "title": "Engineer",
                    "company": "Acme",
                    "location": locations[0],
                    "job_url": "https://example.test/engineer",
                    "description": "",
                }
            ]
        )
        frame.attrs["platform_status"] = {key: "success"}
        return frame

    monkeypatch.setattr(app, "DATABASE_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(app, "fetch_jobs", fetch_one)
    monkeypatch.setattr(app, "enrich_jobs", lambda frame: frame)
    monkeypatch.setattr(app, "filter_jobs", lambda frame, **_kwargs: frame)
    monkeypatch.setattr(app, "save_to_sqlite", lambda *_args: 0)
    monkeypatch.setattr(
        app, "save_to_files", lambda *_args: (tmp_path / "jobs.json", tmp_path / "jobs.csv")
    )
    monkeypatch.setattr(
        app, "save_extraction_run", lambda _path, summary: summaries.append(summary)
    )

    selected, raw_count, valid_count, _dropped, _logs = app.run_extraction(
        ["engineer"],
        ["Pune"],
        ["indeed", "linkedin"],
        10,
        0,
        40,
        None,
    )

    assert len(selected) == 1
    assert raw_count == valid_count == 1
    assert summaries[0]["status"] == "partial"
    assert summaries[0]["platform_status"] == {
        "indeed:Pune:engineer": "failed: 403; browser fallback returned no jobs",
        "linkedin:Pune:engineer": "success",
    }

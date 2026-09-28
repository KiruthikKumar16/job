import sqlite3

import pandas as pd
import pytest
import requests
from jobmarket import scraper
from jobmarket.storage import (
    SCHEMA_VERSION,
    load_jobs_for_dashboard,
    migrate_job_database,
    save_to_sqlite,
)


def test_html_description_is_stored_as_plain_text(tmp_path):
    database = tmp_path / "descriptions.db"
    save_to_sqlite(
        pd.DataFrame(
            [
                {
                    "site": "indeed",
                    "job_url": "https://jobs.example/html",
                    "description": "<h2>Role</h2><p>Build &amp; ship <b>great</b> software.</p>",
                    "description_raw": "<h2>Role</h2><p>Build &amp; ship <b>great</b> software.</p>",
                    "description_status": "ok",
                }
            ]
        ),
        str(database),
    )

    with sqlite3.connect(database) as connection:
        dashboard_frame = load_jobs_for_dashboard(connection)
        raw, status = connection.execute(
            "SELECT description_raw, description_status FROM jobs WHERE job_url = ?",
            ("https://jobs.example/html",),
        ).fetchone()
    assert raw == "Role Build & ship great software."
    assert status == "ok"
    assert "description_raw" not in dashboard_frame.columns


def test_jobspy_description_wins_and_is_cleaned(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "site": "indeed",
                "title": "Engineer",
                "job_url": "https://jobs.example/1",
                "description": "<p>JobSpy <strong>description</strong></p>",
            }
        ]
    )
    normalized = scraper._normalise_jobspy(frame, "indeed")
    monkeypatch.setattr(
        scraper.requests, "get", lambda *args, **kwargs: pytest.fail("unexpected detail request")
    )
    completed = scraper._complete_descriptions(
        normalized, platform="indeed", term="python", location="Pune"
    )
    assert completed.loc[0, "description_raw"] == "JobSpy description"
    assert completed.loc[0, "description_status"] == "ok"


def test_detail_page_is_used_when_jobspy_description_is_missing(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response._content = b"<main><h1>Responsibilities</h1><p>Design reliable systems.</p></main>"
    response.url = "https://jobs.example/detail"
    monkeypatch.setattr(scraper.requests, "get", lambda *args, **kwargs: response)
    frame = pd.DataFrame(
        [
            {
                "site": "linkedin",
                "job_url": response.url,
                "description": "",
                "description_raw": "",
            }
        ]
    )

    completed = scraper._complete_descriptions(
        frame, platform="linkedin", term="engineer", location="Remote"
    )
    assert completed.loc[0, "description_raw"] == "Responsibilities Design reliable systems."
    assert completed.loc[0, "description_status"] == "ok"


@pytest.mark.parametrize("status_code", [403, 429])
def test_blocked_detail_page_sets_blocked_status(monkeypatch, status_code):
    response = requests.Response()
    response.status_code = status_code
    response._content = b"Access denied"
    monkeypatch.setattr(scraper.requests, "get", lambda *args, **kwargs: response)
    frame = pd.DataFrame([{"job_url": "https://jobs.example/blocked", "description": ""}])

    completed = scraper._complete_descriptions(
        frame, platform="indeed", term="analyst", location="Delhi"
    )
    assert completed.loc[0, "description_raw"] == ""
    assert completed.loc[0, "description_status"] == "blocked"


def test_no_description_or_url_is_missing(monkeypatch):
    monkeypatch.setattr(
        scraper.requests, "get", lambda *args, **kwargs: pytest.fail("no URL should be fetched")
    )
    frame = pd.DataFrame([{"job_url": "", "description": None}])
    completed = scraper._complete_descriptions(
        frame, platform="naukri", term="analyst", location="Pune"
    )
    assert completed.loc[0, "description_raw"] == ""
    assert completed.loc[0, "description_status"] == "missing"


def test_legacy_database_migrates_description_columns_without_losing_rows(tmp_path):
    database = tmp_path / "legacy_descriptions.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (site TEXT, title TEXT, description TEXT)")
        connection.execute(
            "INSERT INTO jobs VALUES (?, ?, ?)",
            ("indeed", "Legacy listing", "<p>Legacy <b>description</b></p>"),
        )

    migrate_job_database(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT description_raw, description_status FROM jobs WHERE title = ?",
            ("Legacy listing",),
        ).fetchone() == ("Legacy description", "ok")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        assert {"job_url", "description_raw", "description_status", "requirements_json"} <= columns
        assert (
            connection.execute(
                "SELECT version FROM jobmarket_schema WHERE singleton = 1"
            ).fetchone()[0]
            == SCHEMA_VERSION
        )

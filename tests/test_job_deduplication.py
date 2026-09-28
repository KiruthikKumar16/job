import json
import sqlite3

import pandas as pd
import pytest
from jobmarket.dedup import deduplicate_jobs, normalize_job_field
from jobmarket.storage import save_to_sqlite


def row(
    title,
    company="Acme Inc.",
    location="Bengaluru, India",
    site="linkedin",
    quality=50,
    url="https://example.test/1",
    **extra,
):
    return {
        "title": title,
        "company": company,
        "location": location,
        "site": site,
        "data_quality_score": quality,
        "job_url": url,
        **extra,
    }


def test_normalization_handles_unicode_and_punctuation():
    assert normalize_job_field("  ＤＡＴＡ—Analyst & Engineer! ") == "data analyst and engineer"
    assert normalize_job_field("The Acme, Inc.") == "acme"


def test_fuzzy_duplicate_keeps_best_quality_and_merges_source_list():
    frame = pd.DataFrame(
        [
            row("Data Analyst", site="LinkedIn", quality=70),
            row(
                "Data Analytst",
                company="ACME",
                location="Bengaluru India",
                site="Indeed",
                quality=91,
                url="https://example.test/2",
            ),
        ]
    )

    result = deduplicate_jobs(frame)

    assert len(result) == 1
    assert result.loc[0, "site"] == "Indeed"
    assert result.loc[0, "job_url"] == "https://example.test/2"
    assert result.loc[0, "data_quality_score"] == 91
    assert result.loc[0, "sources"] == ["LinkedIn", "Indeed"]


@pytest.mark.parametrize(
    "other_title",
    [
        "Senior Software Engineer",
        "Software Engineering Manager",
        "Software Developer",
        "Data Analyst",
        "Data Engineer",
    ],
)
def test_distinct_titles_are_not_falsely_merged(other_title):
    frame = pd.DataFrame(
        [row("Software Engineer"), row(other_title, site="Indeed", url="https://example.test/2")]
    )
    assert len(deduplicate_jobs(frame)) == 2


@pytest.mark.parametrize("other_location", ["Hyderabad, India", "Pune, India", "Remote"])
def test_distinct_locations_are_not_merged(other_location):
    frame = pd.DataFrame(
        [row("Data Analyst"), row("Data Analyst", location=other_location, site="Indeed")]
    )
    assert len(deduplicate_jobs(frame)) == 2


def test_threshold_controls_fuzzy_matching():
    frame = pd.DataFrame([row("Data Analyst"), row("Data Analytst", site="Indeed")])
    assert len(deduplicate_jobs(frame, threshold=0.92)) == 1
    assert len(deduplicate_jobs(frame, threshold=0.99)) == 2


def test_numbered_titles_remain_distinct_even_with_high_text_overlap():
    frame = pd.DataFrame(
        [
            row("Analyst 1042"),
            row("Analyst 1043", site="Indeed", url="https://example.test/2"),
        ]
    )
    assert len(deduplicate_jobs(frame)) == 2


def test_missing_identity_fields_never_collapse_unrelated_rows():
    frame = pd.DataFrame(
        [
            row("", company="", location=""),
            row("", company="", location="", site="Indeed", url="https://example.test/2"),
        ]
    )
    assert len(deduplicate_jobs(frame)) == 2


def test_sources_are_merged_without_duplicates_and_ties_keep_first_record():
    frame = pd.DataFrame(
        [
            row("Data Analyst", site="LinkedIn", quality=80, sources=["LinkedIn", "Glassdoor"]),
            row(
                "Data Analyst",
                site="Indeed",
                quality=80,
                url="https://example.test/2",
                sources=["Indeed", "Glassdoor"],
            ),
        ]
    )
    result = deduplicate_jobs(frame)
    assert len(result) == 1
    assert result.loc[0, "job_url"] == "https://example.test/1"
    assert result.loc[0, "sources"] == ["LinkedIn", "Glassdoor", "Indeed"]


def test_empty_input_and_invalid_threshold():
    assert deduplicate_jobs(pd.DataFrame()).empty
    with pytest.raises(ValueError, match="threshold"):
        deduplicate_jobs(pd.DataFrame(), threshold=1.1)


def test_dashboard_filter_columns_get_sqlite_indexes(tmp_path):
    database = tmp_path / "jobs.db"
    save_to_sqlite(
        pd.DataFrame(
            [
                row(
                    "Data Analyst",
                    extracted_skills=["Python", "SQL"],
                    search_term="Analyst",
                    qualification="Bachelor's",
                    seniority="Mid-Level",
                    work_mode="Hybrid",
                    data_quality_score=80,
                    date_posted="2026-09-01",
                    min_exp=2,
                )
            ]
        ),
        str(database),
    )
    with sqlite3.connect(database) as connection:
        names = {item[1] for item in connection.execute("PRAGMA index_list('jobs')")}
    assert {
        "ix_jobs_site",
        "ix_jobs_search_term",
        "ix_jobs_qualification",
        "ix_jobs_extracted_skills",
        "ix_jobs_seniority",
        "ix_jobs_location",
        "ix_jobs_work_mode",
        "ix_jobs_data_quality_score",
        "ix_jobs_date_posted",
        "ix_jobs_min_exp",
        "ix_jobs_dedup_bucket",
    } <= names


def test_separate_writes_merge_cross_source_duplicates_in_sqlite(tmp_path):
    database = tmp_path / "jobs.db"
    save_to_sqlite(pd.DataFrame([row("Data Analyst", site="LinkedIn", quality=70)]), str(database))
    save_to_sqlite(
        pd.DataFrame(
            [
                row(
                    "Data Analytst",
                    company="ACME",
                    location="Bengaluru India",
                    site="Indeed",
                    quality=91,
                    url="https://example.test/2",
                )
            ]
        ),
        str(database),
    )

    with sqlite3.connect(database) as connection:
        records = connection.execute(
            "SELECT site, job_url, data_quality_score, sources FROM jobs"
        ).fetchall()
    assert len(records) == 1
    assert records[0][:3] == ("Indeed", "https://example.test/2", 91)
    assert json.loads(records[0][3]) == ["LinkedIn", "Indeed"]

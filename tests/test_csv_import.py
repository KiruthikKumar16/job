from pathlib import Path

import pandas as pd
import pytest
from jobmarket.csv_import import CSVImportError, read_csv_frame

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("filename", "company", "location"),
    [
        ("utf8_jobs.csv", "Café Labs", "München"),
        ("utf8_sig_jobs.csv", "Café Labs", "München"),
        ("latin1_jobs.csv", "Café Labs", "München"),
    ],
)
def test_reads_common_encodings(filename, company, location):
    frame = read_csv_frame(FIXTURES / filename)
    assert frame.loc[0, "company"] == company
    assert frame.loc[0, "location"] == location


def test_missing_columns_are_preserved_for_normalizer_defaults():
    frame = read_csv_frame(FIXTURES / "missing_columns.csv")
    assert list(frame.columns) == ["title", "job_url"]
    assert frame.loc[0, "title"] == "Analyst"


def test_extra_columns_are_preserved_and_flexible_headers_read():
    frame = read_csv_frame(FIXTURES / "extra_columns.csv")
    assert frame.loc[0, "Job Title"] == "Engineer"
    assert frame.loc[0, "Internal Note"] == "not used"


def test_mixed_date_values_are_not_lost_during_csv_read():
    frame = read_csv_frame(FIXTURES / "mixed_dates.csv")
    assert frame["date_posted"].tolist() == ["2026-09-01", "09/02/2026", "September 3 2026"]
    parsed = pd.to_datetime(frame["date_posted"], format="mixed", errors="coerce", utc=True)
    assert parsed.notna().all()


def test_duplicate_headers_are_disambiguated_by_pandas():
    frame = read_csv_frame(FIXTURES / "duplicate_headers.csv")
    assert frame.columns.tolist() == ["title", "title.1", "company", "job_url"]
    assert frame.loc[0, "title.1"] == "Platform Engineer"


@pytest.mark.parametrize("filename", ["empty.csv"])
def test_empty_csv_has_actionable_error(filename):
    with pytest.raises(CSVImportError, match="empty|header"):
        read_csv_frame(FIXTURES / filename)


def test_malformed_csv_has_actionable_error():
    with pytest.raises(CSVImportError, match="could not be parsed|columns|quotes"):
        read_csv_frame(FIXTURES / "malformed.csv")


def test_large_csv_reads_all_rows(tmp_path):
    path = tmp_path / "large.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write("title,company,location,job_url,description,date_posted\n")
        for index in range(25_000):
            stream.write(
                f"Analyst {index},Example Co,Remote,https://example.test/{index},Python role,2026-09-01\n"
            )
    frame = read_csv_frame(path)
    assert len(frame) == 25_000
    assert frame.loc[24_999, "title"] == "Analyst 24999"

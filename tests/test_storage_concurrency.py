import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from jobmarket.storage import SCHEMA_VERSION, save_to_sqlite


def test_eight_concurrent_writers_are_serialized_and_deduplicated(tmp_path):
    database = tmp_path / "concurrent.db"

    def write_batch(worker_id):
        rows = [
            {
                "site": "linkedin",
                "job_url": f"https://jobs.example/shared/{i}",
                "title": f"Shared {i}",
            }
            for i in range(8)
        ]
        rows.append(
            {
                "site": f"source-{worker_id}",
                "job_url": f"https://jobs.example/unique/{worker_id}",
                "title": f"Unique {worker_id}",
            }
        )
        return save_to_sqlite(pd.DataFrame(rows), str(database))

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(write_batch, range(8))) == [0] * 8

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 16
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM (SELECT site, job_url FROM jobs GROUP BY site, job_url HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT version FROM jobmarket_schema WHERE singleton = 1"
            ).fetchone()[0]
            == SCHEMA_VERSION
        )


def test_additive_schema_migration_preserves_existing_jobs(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (job_url TEXT, title TEXT, legacy_note TEXT)")
        connection.execute(
            "INSERT INTO jobs(job_url, title, legacy_note) VALUES (?, ?, ?)",
            ("https://jobs.example/old", "Old listing", "keep this column"),
        )

    save_to_sqlite(
        pd.DataFrame(
            [
                {
                    "site": "indeed",
                    "job_url": "https://jobs.example/new",
                    "title": "New listing",
                    "description": "New schema field",
                }
            ]
        ),
        str(database),
    )

    with sqlite3.connect(database) as connection:
        old = connection.execute(
            "SELECT title, legacy_note FROM jobs WHERE job_url = ?", ("https://jobs.example/old",)
        ).fetchone()
        assert old == ("Old listing", "keep this column")
        assert connection.execute(
            "SELECT description FROM jobs WHERE job_url = ?", ("https://jobs.example/new",)
        ).fetchone() == ("New schema field",)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        assert {"site", "description", "legacy_note"} <= columns
        assert (
            connection.execute(
                "SELECT version FROM jobmarket_schema WHERE singleton = 1"
            ).fetchone()[0]
            == SCHEMA_VERSION
        )

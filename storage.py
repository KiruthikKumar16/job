"""SQLite and portable-file persistence for job data."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def _valid_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("SQLite table_name must be a simple identifier")
    return value


def save_to_sqlite(df: pd.DataFrame, db_path: str = "jobs.db", table_name: str = "jobs") -> None:
    """Upsert jobs by URL, serialising list/datetime values for SQLite."""
    table = _valid_identifier(table_name)
    frame = df.copy()
    if "job_url" not in frame.columns:
        raise ValueError("DataFrame must contain job_url")
    frame = frame[frame["job_url"].notna() & frame["job_url"].astype(str).str.strip().ne("")]
    for column in frame.columns:
        frame[column] = frame[column].map(lambda value: json.dumps(value) if isinstance(value, list) else value)
        if pd.api.types.is_datetime64_any_dtype(frame[column]):
            frame[column] = frame[column].astype(str)
    with sqlite3.connect(db_path) as connection:
        columns = list(frame.columns)
        definitions = ", ".join(f'"{column}" TEXT' for column in columns)
        connection.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({definitions}, PRIMARY KEY ("job_url"))')
        existing_columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        for column in columns:
            if column not in existing_columns:
                connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" TEXT')
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f'"{column}"=excluded."{column}"' for column in columns if column != "job_url")
        query = f'INSERT INTO "{table}" ({", ".join(chr(34) + col + chr(34) for col in columns)}) VALUES ({placeholders}) '
        query += f'ON CONFLICT("job_url") DO UPDATE SET {assignments}' if assignments else 'ON CONFLICT("job_url") DO NOTHING'
        connection.executemany(query, frame.where(pd.notna(frame), None).itertuples(index=False, name=None))


def save_extraction_run(db_path: str, run: dict[str, object]) -> None:
    """Persist one extraction summary for dashboard health and audit history."""
    columns = ["run_id", "started_at", "finished_at", "raw_count", "valid_count", "status", "platform_status"]
    values = [run.get(column) for column in columns]
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS extraction_runs ("
            "run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, raw_count INTEGER, "
            "valid_count INTEGER, status TEXT, platform_status TEXT)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO extraction_runs "
            "(run_id, started_at, finished_at, raw_count, valid_count, status, platform_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [json.dumps(value) if isinstance(value, (dict, list)) else value for value in values],
        )


def load_extraction_runs(db_path: str, limit: int = 20) -> pd.DataFrame:
    """Read recent extraction summaries, returning an empty frame if unavailable."""
    try:
        with sqlite3.connect(db_path) as connection:
            return pd.read_sql_query(
                "SELECT * FROM extraction_runs ORDER BY started_at DESC LIMIT ?", connection, params=(limit,)
            )
    except (OSError, sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()


def save_to_files(df: pd.DataFrame, base_filename: str = "job_export") -> tuple[Path, Path]:
    """Write UTF-8 timestamped JSON and CSV exports and return their paths."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = Path(base_filename)
    json_path = base.with_name(f"{base.name}_{stamp}").with_suffix(".json")
    csv_path = base.with_name(f"{base.name}_{stamp}").with_suffix(".csv")
    serializable = df.copy()
    if "qualification" not in serializable.columns:
        serializable["qualification"] = "Degree Required"
    else:
        serializable["qualification"] = serializable["qualification"].fillna("").astype(str).replace("", "Degree Required")
    for column in serializable.columns:
        serializable[column] = serializable[column].map(lambda value: json.dumps(value) if isinstance(value, list) else value)
    serializable.to_json(json_path, orient="records", date_format="iso", force_ascii=False, indent=2)
    serializable.to_csv(csv_path, index=False, encoding="utf-8")
    return json_path, csv_path

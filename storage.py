"""SQLite and portable-file persistence for job data."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


LOGGER = logging.getLogger(__name__)


# Shared constant for export base filename
EXPORT_BASE_FILENAME = "job_market_export"


def _valid_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("SQLite table_name must be a simple identifier")
    return value


def _get_column_type_mapping() -> dict[str, str]:
    """Return mapping of column names to desired SQLite types.
    Numeric columns: REAL; others: TEXT (including datetimes stored as ISO strings).
    """
    return {
        "min_exp": "REAL",
        "max_exp": "REAL",
        "data_quality_score": "REAL",
        "salary_min": "REAL",
        "salary_max": "REAL",
        # All other columns default to TEXT (handled elsewhere)
    }


def _ensure_table_schema(connection: sqlite3.Connection, table: str, df_columns: list[str]) -> None:
    """Ensure the table exists with the correct column types.
    If the table exists but has incorrect types, perform a lightweight migration:
    create a new table with correct types, copy data (casting where needed),
    drop the old table, and rename the new table.
    """
    # Get current table info
    cursor = connection.execute(f'PRAGMA table_info("{table}")')
    existing_info = {row[1]: row[2].upper() for row in cursor.fetchall()}  # name -> type

    desired_types = _get_column_type_mapping()
    # Determine which columns we care about (those that appear in df)
    columns_to_ensure = [col for col in df_columns if col in desired_types]

    # Check if any column needs type change or is missing
    need_migration = False
    for col in columns_to_ensure:
        desired = desired_types[col]
        current = existing_info.get(col)
        if current is None:
            need_migration = True  # missing column
            break
        if current != desired:
            need_migration = True  # type mismatch
            break

    if not need_migration:
        # Ensure any missing columns are added (as TEXT by default, but we only add missing non-special columns as TEXT)
        for col in df_columns:
            if col not in existing_info:
                connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" TEXT')
        return

    # Perform migration: create new table with correct schema, copy data, replace old.
    LOGGER.info("Migrating table %s to correct schema", table)
    # Build new column definitions
    new_defs = []
    for col in df_columns:
        col_type = desired_types.get(col, "TEXT")
        new_defs.append(f'"{col}" {col_type}')
    new_defs.append('PRIMARY KEY ("job_url")')
    new_table = f"{table}_new"
    connection.execute(f'CREATE TABLE "{new_table}" ({", ".join(new_defs)})')

    # Copy data, casting numeric columns appropriately
    # We'll select all columns, using CAST for numeric columns where needed.
    select_parts = []
    for col in df_columns:
        if col in desired_types and desired_types[col] == "REAL":
            select_parts.append(f'CAST("{col}" AS REAL) AS "{col}"')
        else:
            select_parts.append(f'"{col}"')
    select_clause = ", ".join(select_parts)
    connection.execute(f'INSERT INTO "{new_table}" ({", ".join(f'"{col}"' for col in df_columns)}) '
                       f'SELECT {select_clause} FROM "{table}"')

    # Drop old table and rename new
    connection.execute(f'DROP TABLE "{table}"')
    connection.execute(f'ALTER TABLE "{new_table}" RENAME TO "{table}"')
    connection.commit()


def save_to_sqlite(df: pd.DataFrame, db_path: str = "jobs.db", table_name: str = "jobs") -> int:
    """Upsert jobs by URL, serialising list/datetime values for SQLite.

    Returns:
        int: Number of rows dropped due to missing job_url
    """
    table = _valid_identifier(table_name)
    frame = df.copy()
    if "job_url" not in frame.columns:
        raise ValueError("DataFrame must contain job_url")

    # Count rows with missing job_url before filtering
    missing_job_url_count = frame["job_url"].isna().sum() + (frame["job_url"].astype(str).str.strip() == "").sum()

    frame = frame[frame["job_url"].notna() & frame["job_url"].astype(str).str.strip().ne("")]

    if missing_job_url_count > 0:
        LOGGER.warning("Dropped %d rows with missing job_url before persisting", missing_job_url_count)

    for column in frame.columns:
        frame[column] = frame[column].map(lambda value: json.dumps(value) if isinstance(value, list) else value)
        if pd.api.types.is_datetime64_any_dtype(frame[column]):
            frame[column] = frame[column].astype(str)

    with sqlite3.connect(db_path) as connection:
        # Ensure table has correct schema (including migration if needed)
        _ensure_table_schema(connection, table, list(frame.columns))

        columns = list(frame.columns)
        # Build definitions with proper types
        type_mapping = _get_column_type_mapping()
        definitions = []
        for col in columns:
            col_type = type_mapping.get(col, "TEXT")
            definitions.append(f'"{col}" {col_type}')
        definitions.append('PRIMARY KEY ("job_url")')
        connection.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(definitions)})')

        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f'"{col}"=excluded."{col}"' for col in columns if col != "job_url")
        query = f'INSERT INTO "{table}" ({", ".join(chr(34) + col + chr(34) for col in columns)}) VALUES ({placeholders}) '
        query += f'ON CONFLICT("job_url") DO UPDATE SET {assignments}' if assignments else 'ON CONFLICT("job_url") DO NOTHING'
        connection.executemany(query, frame.where(pd.notna(frame), None).itertuples(index=False, name=None))

    return missing_job_url_count


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


def save_to_files(df: pd.DataFrame, base_filename: str = EXPORT_BASE_FILENAME) -> tuple[Path, Path]:
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
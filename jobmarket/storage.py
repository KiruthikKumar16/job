"""SQLite and portable-file persistence for job data."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from jobmarket.dedup import deduplicate_jobs, job_dedup_bucket

LOGGER = logging.getLogger(__name__)


# Shared constant for export base filename
EXPORT_BASE_FILENAME = "job_market_export"
SCHEMA_VERSION = 6
SQLITE_TIMEOUT_SECONDS = 30
_DASHBOARD_INDEX_COLUMNS = (
    "site",
    "search_term",
    "qualification",
    "extracted_skills",
    "seniority",
    "location",
    "work_mode",
    "data_quality_score",
    "date_posted",
    "min_exp",
    "dedup_bucket",
)
_DB_LOCKS: dict[str, threading.RLock] = {}
_DB_LOCKS_GUARD = threading.Lock()


def _valid_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("SQLite table_name must be a simple identifier")
    return value


def _database_lock(db_path: str | Path) -> threading.RLock:
    key = str(Path(db_path).resolve())
    with _DB_LOCKS_GUARD:
        return _DB_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _connection(db_path: str | Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a configured connection and always close it; serialize local writers."""
    lock = _database_lock(db_path) if write else None
    if lock:
        lock.acquire()
    connection = None
    try:
        connection = sqlite3.connect(str(db_path), timeout=SQLITE_TIMEOUT_SECONDS)
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
        if write:
            connection.execute("PRAGMA journal_mode = WAL")
            with connection:
                yield connection
        else:
            yield connection
    finally:
        if connection is not None:
            connection.close()
        if lock:
            lock.release()


def _ensure_schema_version(connection: sqlite3.Connection) -> None:
    """Record the additive schema migration level without rewriting user tables."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS jobmarket_schema "
        "(singleton INTEGER PRIMARY KEY CHECK(singleton = 1), version INTEGER NOT NULL)"
    )
    row = connection.execute("SELECT version FROM jobmarket_schema WHERE singleton = 1").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO jobmarket_schema(singleton, version) VALUES (1, ?)", (SCHEMA_VERSION,)
        )
    elif row[0] > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {row[0]} is newer than supported version {SCHEMA_VERSION}"
        )
    elif row[0] < SCHEMA_VERSION:
        connection.execute(
            "UPDATE jobmarket_schema SET version = ? WHERE singleton = 1", (SCHEMA_VERSION,)
        )


def migrate_job_database(db_path: str | Path, table_name: str = "jobs") -> None:
    """Apply additive job-table migrations when an existing database is opened."""
    table = _valid_identifier(table_name)
    with _connection(db_path, write=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if exists:
            _ensure_table_schema(
                connection,
                table,
                [
                    "site",
                    "job_url",
                    "description_raw",
                    "description_status",
                    "requirements_json",
                    "sources",
                    "dedup_bucket",
                ],
            )


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
    """Apply additive migrations while preserving all existing columns and rows."""
    desired_types = _get_column_type_mapping()
    df_columns = list(
        dict.fromkeys(
            [
                *df_columns,
                "job_url",
                "description_raw",
                "description_status",
                "requirements_json",
                "sources",
                "dedup_bucket",
            ]
        )
    )
    existing_info = {
        row[1]: row for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    if not existing_info:
        definitions = [f'"{column}" {desired_types.get(column, "TEXT")}' for column in df_columns]
        connection.execute(f'CREATE TABLE "{table}" ({", ".join(definitions)})')
        existing_info = {
            row[1]: row for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        }

    # Never rebuild/drop a pre-existing table just to adjust affinity. SQLite
    # stores values dynamically, and additive columns preserve legacy data.
    for column in df_columns:
        if column not in existing_info:
            connection.execute(
                f'ALTER TABLE "{table}" ADD COLUMN "{column}" {desired_types.get(column, "TEXT")}'
            )

    current_columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    for column in _DASHBOARD_INDEX_COLUMNS:
        if column in current_columns:
            index_name = _valid_identifier(f"ix_{table}_{column}")
            connection.execute(
                f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table}" ("{column}")'
            )

    # Backfill new fields once, preserving legacy rows without rewriting large text columns
    # on every subsequent upsert.
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    added_description_raw = "description_raw" not in existing_info
    added_description_status = "description_status" not in existing_info
    added_requirements = "requirements_json" not in existing_info
    if added_description_raw and "description" in columns:
        connection.execute(
            f'UPDATE "{table}" SET description_raw = description '
            "WHERE (description_raw IS NULL OR trim(description_raw) = '') "
            "AND description IS NOT NULL AND trim(description) <> ''"
        )
    if added_description_raw:
        description_rows = connection.execute(
            f'SELECT rowid, description_raw FROM "{table}" '
            "WHERE description_raw IS NOT NULL AND trim(description_raw) <> ''"
        ).fetchall()
        connection.executemany(
            f'UPDATE "{table}" SET description_raw = ? WHERE rowid = ?',
            [(_plain_text(value), rowid) for rowid, value in description_rows],
        )
    if added_description_status:
        connection.execute(
            f'UPDATE "{table}" SET description_status = '
            "CASE WHEN description_raw IS NOT NULL AND trim(description_raw) <> '' THEN 'ok' ELSE 'missing' END "
            "WHERE description_status IS NULL OR description_status NOT IN ('ok', 'blocked', 'missing') "
            "OR (description_status = 'missing' AND description_raw IS NOT NULL AND trim(description_raw) <> '') "
            "OR (description_status = 'ok' AND (description_raw IS NULL OR trim(description_raw) = ''))"
        )
    if {"title", "company", "location", "dedup_bucket"} <= columns:
        identity_rows = connection.execute(
            f'SELECT rowid, title, company, location FROM "{table}" WHERE dedup_bucket IS NULL'
        ).fetchall()
        connection.executemany(
            f'UPDATE "{table}" SET dedup_bucket = ? WHERE rowid = ?',
            [
                (job_dedup_bucket(title, company, location), rowid)
                for rowid, title, company, location in identity_rows
            ],
        )
    from jobmarket.job_parser import extract_requirements

    if added_requirements:
        requirement_rows = connection.execute(
            f'SELECT rowid, description_raw FROM "{table}" '
            "WHERE requirements_json IS NULL OR trim(requirements_json) = ''"
        ).fetchall()
        connection.executemany(
            f'UPDATE "{table}" SET requirements_json = ? WHERE rowid = ?',
            [
                (
                    json.dumps(
                        extract_requirements(value or ""), ensure_ascii=False, sort_keys=True
                    ),
                    rowid,
                )
                for rowid, value in requirement_rows
            ],
        )

    index_name = _valid_identifier(f"ux_{table}_site_job_url")
    index_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (index_name,)
    ).fetchone()
    if not index_exists:
        connection.execute(f'UPDATE "{table}" SET "site" = \'\' WHERE "site" IS NULL')
        # Old databases may predate a unique key. Collapse only duplicate
        # source/URL rows (keeping the latest row) before adding the index.
        connection.execute(
            f'DELETE FROM "{table}" WHERE "job_url" IS NOT NULL AND trim("job_url") <> \'\' '
            f'AND rowid NOT IN (SELECT MAX(rowid) FROM "{table}" '
            f'WHERE "job_url" IS NOT NULL AND trim("job_url") <> \'\' GROUP BY "site", "job_url")'
        )
        connection.execute(f'CREATE UNIQUE INDEX "{index_name}" ON "{table}" ("site", "job_url")')
    _ensure_schema_version(connection)


def save_to_sqlite(df: pd.DataFrame, db_path: str = "jobs.db", table_name: str = "jobs") -> int:
    """Atomically upsert jobs by source and URL, serialising values for SQLite.

    Returns:
        int: Number of rows dropped due to missing job_url
    """
    table = _valid_identifier(table_name)
    frame = df.copy()
    if "job_url" not in frame.columns:
        raise ValueError("DataFrame must contain job_url")

    # Count rows with missing job_url before filtering
    missing_job_url_count = (
        frame["job_url"].isna().sum() + (frame["job_url"].astype(str).str.strip() == "").sum()
    )

    frame["job_url"] = frame["job_url"].astype("string").str.strip()
    frame = frame[frame["job_url"].notna() & frame["job_url"].ne("")]

    if "site" not in frame.columns:
        frame["site"] = frame["source"] if "source" in frame.columns else "unknown"
    frame["site"] = frame["site"].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    if {"title", "company", "location"} <= set(frame.columns):
        frame["dedup_bucket"] = frame.apply(
            lambda row: job_dedup_bucket(row["title"], row["company"], row["location"]), axis=1
        )
    else:
        frame["dedup_bucket"] = ""
    candidate_buckets = {bucket for bucket in frame["dedup_bucket"] if bucket}

    if "description_raw" not in frame.columns:
        frame["description_raw"] = (
            frame["description"]
            if "description" in frame.columns
            else pd.Series("", index=frame.index)
        )
    frame["description_raw"] = frame["description_raw"].fillna("").map(_plain_text)
    if "description" in frame.columns:
        fallback_description = frame["description"].fillna("").map(_plain_text)
        missing_raw = frame["description_raw"].eq("")
        frame.loc[missing_raw, "description_raw"] = fallback_description.loc[missing_raw]
    if "description_status" not in frame.columns:
        frame["description_status"] = frame["description_raw"].map(
            lambda value: "ok" if value else "missing"
        )
    frame["description_status"] = frame["description_status"].fillna("missing").astype(str)
    invalid_status = ~frame["description_status"].isin({"ok", "blocked", "missing"})
    frame.loc[invalid_status, "description_status"] = frame.loc[
        invalid_status, "description_raw"
    ].map(lambda value: "ok" if value else "missing")
    inconsistent = (frame["description_status"].eq("missing") & frame["description_raw"].ne("")) | (
        frame["description_status"].eq("ok") & frame["description_raw"].eq("")
    )
    frame.loc[inconsistent, "description_status"] = frame.loc[inconsistent, "description_raw"].map(
        lambda value: "ok" if value else "missing"
    )
    if "requirements_json" not in frame.columns:
        from jobmarket.job_parser import extract_requirements

        frame["requirements_json"] = frame["description_raw"].map(
            lambda value: json.dumps(
                extract_requirements(value), ensure_ascii=False, sort_keys=True
            )
        )
    else:

        def normalize_requirements(value: object, description: str) -> str:
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
            if isinstance(value, str) and value.strip():
                try:
                    return json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True)
                except (TypeError, ValueError):
                    pass
            from jobmarket.job_parser import extract_requirements

            return json.dumps(extract_requirements(description), ensure_ascii=False, sort_keys=True)

        fallback_descriptions = frame["description_raw"].astype(str)
        frame["requirements_json"] = [
            normalize_requirements(value, description)
            for value, description in zip(
                frame["requirements_json"], fallback_descriptions, strict=True
            )
        ]

    if missing_job_url_count > 0:
        LOGGER.warning(
            "Dropped %d rows with missing job_url before persisting", missing_job_url_count
        )

    for column in frame.columns:
        _valid_identifier(str(column))
        frame[column] = frame[column].map(
            lambda value: json.dumps(value) if isinstance(value, list) else value
        )
        if pd.api.types.is_datetime64_any_dtype(frame[column]):
            frame[column] = frame[column].astype(str)

    with _connection(db_path, write=True) as connection:
        _ensure_table_schema(connection, table, list(frame.columns))
        columns = list(frame.columns)
        placeholders = ", ".join("?" for _ in columns)
        info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        has_url_primary_key = any(row[1] == "job_url" and row[5] for row in info)
        has_url_unique_index = any(
            unique
            and [item[2] for item in connection.execute(f'PRAGMA index_info("{index_name}")')]
            == ["job_url"]
            for index_name, unique, *_ in connection.execute(f'PRAGMA index_list("{table}")')
        )
        conflict_target = (
            '"job_url"' if has_url_primary_key or has_url_unique_index else '"site", "job_url"'
        )
        assignments = ", ".join(
            f'"{col}"=excluded."{col}"' for col in columns if col not in {"site", "job_url"}
        )
        query = f'INSERT INTO "{table}" ({", ".join(chr(34) + col + chr(34) for col in columns)}) VALUES ({placeholders}) '
        query += (
            f"ON CONFLICT({conflict_target}) DO UPDATE SET {assignments}"
            if assignments
            else f"ON CONFLICT({conflict_target}) DO NOTHING"
        )
        connection.executemany(
            query, frame.where(pd.notna(frame), None).itertuples(index=False, name=None)
        )
        _deduplicate_stored_jobs(connection, table, candidate_buckets)

    return missing_job_url_count


def _deduplicate_stored_jobs(
    connection: sqlite3.Connection, table: str, candidate_buckets: set[str]
) -> None:
    """Prune fuzzy duplicates after an upsert, merging source names atomically."""
    if not candidate_buckets:
        return
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    if not {"title", "company", "location", "dedup_bucket"} <= columns:
        return
    connection.execute(
        "CREATE TEMP TABLE IF NOT EXISTS jobmarket_dedup_candidates (bucket TEXT PRIMARY KEY)"
    )
    connection.execute("DELETE FROM jobmarket_dedup_candidates")
    connection.executemany(
        "INSERT OR IGNORE INTO jobmarket_dedup_candidates(bucket) VALUES (?)",
        [(bucket,) for bucket in candidate_buckets],
    )
    selected_columns = [
        column
        for column in (
            "site",
            "sources",
            "title",
            "company",
            "location",
            "data_quality_score",
            "dedup_bucket",
        )
        if column in columns
    ]
    selection = ", ".join(f'"{column}"' for column in selected_columns)
    rows = pd.read_sql_query(
        f'SELECT rowid AS _jobmarket_rowid, {selection} FROM "{table}" '
        "WHERE dedup_bucket IN (SELECT bucket FROM jobmarket_dedup_candidates)",
        connection,
    )
    deduplicated = deduplicate_jobs(rows)
    winners = set(
        pd.to_numeric(deduplicated["_jobmarket_rowid"], errors="coerce").dropna().astype(int)
    )
    all_rowids = set(pd.to_numeric(rows["_jobmarket_rowid"], errors="coerce").dropna().astype(int))
    connection.executemany(
        f'UPDATE "{table}" SET "sources" = ? WHERE rowid = ?',
        [
            (json.dumps(row["sources"], ensure_ascii=False), int(row["_jobmarket_rowid"]))
            for _, row in deduplicated.iterrows()
        ],
    )
    losers = sorted(all_rowids - winners)
    if losers:
        connection.executemany(
            f'DELETE FROM "{table}" WHERE rowid = ?', [(rowid,) for rowid in losers]
        )
    connection.execute("DELETE FROM jobmarket_dedup_candidates")


def _plain_text(value: object) -> str:
    """Remove markup and normalize whitespace before persisting a raw description."""
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self.hidden_depth = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.casefold() in {"script", "style", "noscript"}:
                self.hidden_depth += 1

        def handle_endtag(self, tag: str) -> None:
            if tag.casefold() in {"script", "style", "noscript"} and self.hidden_depth:
                self.hidden_depth -= 1

        def handle_data(self, data: str) -> None:
            if not self.hidden_depth:
                self.parts.append(data)

    text = "" if value is None else str(value)
    extractor = TextExtractor()
    extractor.feed(text)
    extractor.close()
    return " ".join(html.unescape(" ".join(extractor.parts)).split())


def save_extraction_run(db_path: str, run: dict[str, object]) -> None:
    """Persist one extraction summary for dashboard health and audit history."""
    columns = [
        "run_id",
        "started_at",
        "finished_at",
        "raw_count",
        "valid_count",
        "status",
        "platform_status",
    ]
    values = [run.get(column) for column in columns]
    with _connection(db_path, write=True) as connection:
        _ensure_schema_version(connection)
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


def load_jobs_for_dashboard(
    connection: sqlite3.Connection, table_name: str = "jobs"
) -> pd.DataFrame:
    """Read job-list fields while leaving potentially large descriptions in SQLite."""
    table = _valid_identifier(table_name)
    columns = [
        row[1]
        for row in connection.execute(f'PRAGMA table_info("{table}")')
        if row[1] != "description_raw"
    ]
    if not columns:
        return pd.DataFrame()
    selected = ", ".join(f'"{column}"' for column in columns)
    return pd.read_sql_query(f'SELECT {selected} FROM "{table}"', connection)


def load_extraction_runs(db_path: str, limit: int = 20) -> pd.DataFrame:
    """Read recent extraction summaries, returning an empty frame if unavailable."""
    try:
        with _connection(db_path) as connection:
            return pd.read_sql_query(
                "SELECT * FROM extraction_runs ORDER BY started_at DESC LIMIT ?",
                connection,
                params=(limit,),
            )
    except (OSError, sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()


def save_to_files(df: pd.DataFrame, base_filename: str = EXPORT_BASE_FILENAME) -> tuple[Path, Path]:
    """Write UTF-8 timestamped JSON and CSV exports and return their paths."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = Path(base_filename)
    json_path = base.with_name(f"{base.name}_{stamp}").with_suffix(".json")
    csv_path = base.with_name(f"{base.name}_{stamp}").with_suffix(".csv")
    serializable = df.copy()
    if "qualification" not in serializable.columns:
        serializable["qualification"] = "Degree Required"
    else:
        serializable["qualification"] = (
            serializable["qualification"].fillna("").astype(str).replace("", "Degree Required")
        )
    for column in serializable.columns:
        serializable[column] = serializable[column].map(
            lambda value: json.dumps(value) if isinstance(value, list) else value
        )
    json_temp = json_path.with_name(f".{json_path.name}.{uuid4().hex}.tmp")
    csv_temp = csv_path.with_name(f".{csv_path.name}.{uuid4().hex}.tmp")
    try:
        serializable.to_json(
            json_temp, orient="records", date_format="iso", force_ascii=False, indent=2
        )
        serializable.to_csv(csv_temp, index=False, encoding="utf-8")
        os.replace(json_temp, json_path)
        os.replace(csv_temp, csv_path)
    finally:
        json_temp.unlink(missing_ok=True)
        csv_temp.unlink(missing_ok=True)
    return json_path, csv_path

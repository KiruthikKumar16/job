"""Interactive Streamlit UI for the job scraping and analytics pipeline."""

from __future__ import annotations

import ast
import difflib
import json
import logging
import math
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from jobmarket.csv_import import CSVImportError, read_csv_frame
from jobmarket.dedup import deduplicate_jobs
from jobmarket.job_filters import filter_jobs
from jobmarket.job_parser import _data_quality_score, enrich_jobs, extract_requirements
from jobmarket.scraper import NORMALIZED_COLUMNS, fetch_jobs
from jobmarket.storage import (
    EXPORT_BASE_FILENAME,
    load_extraction_runs,
    load_jobs_for_dashboard,
    migrate_job_database,
    save_extraction_run,
    save_to_files,
    save_to_sqlite,
)

BASE_DIR = Path(__file__).resolve().parent.parent
# Use environment variable for database path, fallback to default
default_db_path = os.environ.get("JOB_DATABASE_PATH", "jobs.db")
DATABASE_PATH = BASE_DIR / default_db_path
PLATFORM_LABELS = {
    "LinkedIn": "linkedin",
    "Indeed": "indeed",
    "Glassdoor": "glassdoor",
    "Naukri": "naukri",
}
LOCATION_OPTIONS = ["Bengaluru", "Hyderabad", "Pune", "Mumbai", "Chennai", "Delhi NCR"]
ROLE_OPTIONS = [
    "Data Analyst",
    "Data Engineer",
    "Full Stack Developer",
    "Backend Developer",
    "Frontend Developer",
    "Python Developer",
    "Machine Learning Engineer",
    "Cloud Engineer",
]
DASHBOARD_COLUMNS = {
    "site": "Source",
    "search_term": "Job Role",
    "title": "Title",
    "company": "Company",
    "location": "Location",
    "qualification": "Parsed Qualification",
    "extracted_skills": "Extracted Skills",
    "seniority": "Seniority",
    "min_exp": "Min Exp",
    "max_exp": "Max Exp",
    "work_mode": "Work Mode",
    "data_quality_score": "Quality Score",
    "date_posted": "Posted",
    "job_url": "Apply URL",
}


@st.cache_data(ttl=300, show_spinner=False)
def load_jobs(database_path: str, data_directory: str, cache_version: str = "") -> pd.DataFrame:
    """Load the SQLite dataset, falling back to the newest portable export."""
    database = Path(database_path)
    if database.exists():
        try:
            migrate_job_database(database)
            with closing(sqlite3.connect(database, timeout=30)) as connection:
                tables = pd.read_sql_query(
                    "SELECT name FROM sqlite_master WHERE type='table'", connection
                )
                if "jobs" in tables["name"].tolist():
                    jobs = load_jobs_for_dashboard(connection)
                    if not jobs.empty or len(jobs.columns):
                        return _prepare_frame(jobs)
        except (OSError, sqlite3.Error, pd.errors.DatabaseError):
            pass

    directory = Path(data_directory)
    exports = sorted(
        directory.glob(f"{EXPORT_BASE_FILENAME}_*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if exports:
        return _prepare_frame(pd.read_csv(exports[0]))
    json_exports = sorted(
        directory.glob(f"{EXPORT_BASE_FILENAME}_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if json_exports:
        return _prepare_frame(pd.read_json(json_exports[0]))
    return pd.DataFrame()


def _dashboard_cache_version(database_path: str, data_directory: str) -> str:
    """Build a cache key from the active SQLite file or fallback exports."""
    paths = [Path(database_path), Path(f"{database_path}-wal")]
    if not Path(database_path).exists():
        directory = Path(data_directory)
        paths.extend(directory.glob(f"{EXPORT_BASE_FILENAME}_*.csv"))
        paths.extend(directory.glob(f"{EXPORT_BASE_FILENAME}_*.json"))
    stamps = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            stamps.append(f"{path}:missing")
        else:
            stamps.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(stamps)


def invalidate_dashboard_cache() -> None:
    """Clear cached dashboard data after application writes or user refresh."""
    load_jobs.clear()
    load_csv_file.clear()


def _column_key(value: str) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


IMPORT_ALIASES = {
    "title": ["title", "jobtitle", "jobname", "position", "role", "designation", "jobrole"],
    "company": [
        "company",
        "companyname",
        "employer",
        "organization",
        "organisation",
        "hiringcompany",
    ],
    "location": ["location", "joblocation", "city", "place", "worklocation", "勤務地"],
    "job_url": ["joburl", "url", "applyurl", "applicationurl", "link", "joblink", "applylink"],
    "description": [
        "description",
        "jobdescription",
        "jobdetails",
        "details",
        "summary",
        "aboutthejob",
        "content",
    ],
    "date_posted": ["dateposted", "posteddate", "postingdate", "date", "published", "createdat"],
    "qualification": [
        "qualification",
        "degree",
        "education",
        "educationalqualification",
        "degree要求",
    ],
    "extracted_skills": [
        "skills",
        "skill",
        "requiredskills",
        "technologies",
        "techstack",
        "competencies",
    ],
    "min_exp": [
        "minexp",
        "minimumexperience",
        "experienceyears",
        "yearsofexperience",
        "experience",
    ],
    "max_exp": ["maxexp", "maximumexperience", "experienceto"],
    "site": ["site", "source", "platform", "jobboard", "portal"],
}


def _match_import_columns(columns: list[str]) -> dict[str, str]:
    """Map flexible CSV headers to canonical fields using aliases and fuzzy NLP-like tokens."""
    normalized = {_column_key(column): column for column in columns}
    matches: dict[str, str] = {}
    for target, aliases in IMPORT_ALIASES.items():
        alias_keys = [_column_key(alias) for alias in aliases]
        exact = next((normalized[key] for key in alias_keys if key in normalized), None)
        if exact:
            matches[target] = exact
            continue
        candidates = difflib.get_close_matches(target, list(normalized), n=1, cutoff=0.72)
        if candidates:
            matches[target] = normalized[candidates[0]]
    return matches


def normalize_imported_csv(frame: pd.DataFrame, source_name: str = "csv") -> pd.DataFrame:
    """Normalize arbitrary CSV columns, then enrich records with the shared NLP pipeline."""
    matches = _match_import_columns(list(frame.columns))
    normalized = pd.DataFrame(index=frame.index)
    for target, column in matches.items():
        normalized[target] = frame[column]
    for column in NORMALIZED_COLUMNS:
        if column not in normalized:
            normalized[column] = ""
    if "site" not in matches:
        normalized["site"] = source_name
    if "search_term" not in normalized:
        normalized["search_term"] = "Imported CSV"
    normalized = normalized.reindex(columns=NORMALIZED_COLUMNS + ["search_term"])
    return _prepare_frame(enrich_jobs(normalized))


@st.cache_data(ttl=300, show_spinner=False)
def load_csv_file(path: str, cache_version: str = "") -> pd.DataFrame:
    return normalize_imported_csv(read_csv_frame(path), Path(path).stem)


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in (
        "site",
        "search_term",
        "title",
        "company",
        "location",
        "qualification",
        "seniority",
        "job_url",
        "work_mode",
    ):
        if column not in result.columns:
            result[column] = ""
        result[column] = result[column].fillna("").astype(str)
    if "description" not in result.columns:
        result["description"] = ""
    result["description"] = result["description"].fillna("").astype(str)
    if "extracted_skills" not in result.columns:
        result["extracted_skills"] = [[] for _ in range(len(result))]
    result["extracted_skills"] = result["extracted_skills"].map(_parse_skills)
    for column in ("min_exp", "max_exp"):
        if column not in result.columns:
            result[column] = pd.NA
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if "date_posted" not in result.columns:
        result["date_posted"] = pd.NaT
    result["date_posted"] = pd.to_datetime(
        result["date_posted"], format="mixed", errors="coerce", utc=True
    )
    result["qualification"] = result["qualification"].replace({"": "Degree Required"})
    if "data_quality_score" not in result.columns:
        result["data_quality_score"] = result.apply(_data_quality_score, axis=1)
    else:
        result["data_quality_score"] = pd.to_numeric(result["data_quality_score"], errors="coerce")
        missing_quality = result["data_quality_score"].isna()
        if missing_quality.any():
            result.loc[missing_quality, "data_quality_score"] = result.loc[missing_quality].apply(
                _data_quality_score, axis=1
            )
    if "sources" not in result.columns:
        result["sources"] = [[] for _ in range(len(result))]
    else:
        result["sources"] = result["sources"].map(_parse_sources)
    result["sources"] = result.apply(
        lambda row: list(dict.fromkeys([*row["sources"], *([row["site"]] if row["site"] else [])])),
        axis=1,
    )
    return deduplicate_jobs(result)


def _parse_sources(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return list(
            dict.fromkeys(
                str(item).strip() for item in value if item is not None and str(item).strip()
            )
        )
    if value is None or (not isinstance(value, (dict, list)) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return list(dict.fromkeys(str(item).strip() for item in parsed if str(item).strip()))
    except (ValueError, TypeError):
        pass
    return [item.strip() for item in text.split(",") if item.strip()]


def _parse_skills(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return [item.strip() for item in text.split(",") if item.strip()]


class _LogBuffer(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


@contextmanager
def capture_pipeline_logs() -> Iterator[_LogBuffer]:
    handler = _LogBuffer()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)


def _parse_terms(raw_terms: str) -> list[str]:
    return [term.strip() for term in raw_terms.split(",") if term.strip()]


def _parse_proxies(raw_proxies: str) -> list[str] | None:
    values = [value.strip() for value in raw_proxies.replace("\n", ",").split(",") if value.strip()]
    return values or None


def _window_hours(amount: float, unit: str) -> int:
    return max(1, round(amount * (24 if unit == "Days" else 1)))


def _reset_state(keys: list[str]) -> None:
    for key in keys:
        st.session_state.pop(key, None)
    st.rerun()


def _search_worker_count(search_count: int) -> int:
    """Return the same worker count used by extraction for ETA calculations."""
    configured_workers = os.environ.get("JOB_MAX_WORKERS")
    try:
        worker_limit = max(1, int(configured_workers)) if configured_workers else 6
    except ValueError:
        worker_limit = 6
    return min(worker_limit, search_count) if search_count else 1


def _estimate_extraction_seconds(search_count: int, max_results: int | None) -> int:
    """Estimate wall time using a conservative per-query budget and actual concurrency."""
    if search_count <= 0:
        return 0
    workers = _search_worker_count(search_count)
    seconds_per_search = 120 if max_results is None else 20 + min(max_results, 200) * 0.6
    return math.ceil(math.ceil(search_count / workers) * seconds_per_search + 30)


def _format_duration(seconds: float) -> str:
    """Format an ETA in short, human-readable units."""
    rounded_seconds = max(0, math.ceil(seconds))
    if rounded_seconds < 60:
        return f"{rounded_seconds} sec"
    minutes, remaining_seconds = divmod(rounded_seconds, 60)
    if minutes < 60:
        return f"{minutes} min {remaining_seconds} sec" if remaining_seconds else f"{minutes} min"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours} hr {remaining_minutes} min" if remaining_minutes else f"{hours} hr"


def run_extraction(
    terms: list[str],
    locations: list[str],
    platforms: list[str],
    max_results: int | None,
    min_exp: float,
    max_exp: float,
    proxies: list[str] | None,
    hours_old: int | None = None,
    progress_callback: Callable[[int, int, str], Any] | None = None,
    use_public_proxies: bool = False,
    status_callback: Callable[[str], Any] | None = None,
) -> tuple[pd.DataFrame, int, int, int, list[str]]:
    """Fetch each query independently so blocked sources cannot stall the UI."""
    combinations = [
        (term, location, platform)
        for term in terms
        for location in locations
        for platform in platforms
    ]
    started_at = datetime.now(timezone.utc)
    raw_frames: list[pd.DataFrame] = []
    platform_status: dict[str, str] = {}

    # Normalize proxies once for the entire extraction
    normalized_proxies = []
    if proxies:
        from jobmarket.scraper import _normalise_user_proxies

        normalized_proxies = _normalise_user_proxies(proxies) or []

    with capture_pipeline_logs() as logs:
        if status_callback:
            status_callback(
                f"Starting {len(combinations)} searches across {len(platforms)} sources."
            )

        def collect_one(
            combination: tuple[str, str, str], task_index: int
        ) -> tuple[tuple[str, str, str], pd.DataFrame | None, str]:
            term, location, platform = combination
            # Assign proxy based on task index for rotation across all tasks
            proxy = (
                normalized_proxies[task_index % len(normalized_proxies)]
                if normalized_proxies
                else None
            )
            try:
                frame = fetch_jobs(
                    [term],
                    [location],
                    [platform],
                    max_results=max_results,
                    proxies=None,  # Pass None since we're handling rotation manually
                    proxy=proxy,  # Pass the specific proxy for this task
                    hours_old=hours_old,
                    use_public_proxies=use_public_proxies,
                )
                query_key = f"{platform}:{location}:{term}"
                status = frame.attrs.get("platform_status", {}).get(query_key)
                return combination, frame, status or ("success" if not frame.empty else "empty")
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Continuing after %s failed for %s at %s: %s",
                    platform.title(),
                    term,
                    location,
                    error,
                )
                return combination, None, f"failed: {error}"

        worker_count = _search_worker_count(len(combinations))
        # Search workers only return frames. SQLite persistence happens once,
        # after all futures finish, through storage's per-database writer lock.
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            # Submit all tasks with their indices for proxy rotation
            futures = [
                executor.submit(collect_one, combination, index)
                for index, combination in enumerate(combinations)
            ]
            for index, future in enumerate(as_completed(futures), start=1):
                (term, location, platform), frame, status = future.result()
                platform_status[f"{platform}:{location}:{term}"] = status
                if frame is not None and not frame.empty:
                    raw_frames.append(frame)
                result_count = len(frame) if frame is not None else 0
                work_label = (
                    f"{platform.title()} | {term} | {location}: {status} ({result_count} listings)"
                )
                if progress_callback:
                    progress_callback(index, len(combinations), work_label)
        if status_callback:
            status_callback("All searches finished. Enriching and deduplicating listings.")
        raw = (
            pd.concat(raw_frames, ignore_index=True)
            if raw_frames
            else pd.DataFrame(columns=NORMALIZED_COLUMNS)
        )
        enriched = deduplicate_jobs(enrich_jobs(raw))
        if status_callback:
            status_callback("Saving the database, export files, and extraction summary.")
        dropped_count = save_to_sqlite(enriched, str(DATABASE_PATH), "jobs")
        save_to_files(enriched, str(BASE_DIR / EXPORT_BASE_FILENAME))
        valid_count = int(
            enriched.get("title", pd.Series(dtype=str)).astype(str).str.strip().ne("").sum()
        )
        save_extraction_run(
            str(DATABASE_PATH),
            {
                "run_id": str(uuid4()),
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "raw_count": len(raw),
                "valid_count": valid_count,
                "status": "partial"
                if any(value.startswith("failed:") for value in platform_status.values())
                else "completed",
                "platform_status": platform_status,
            },
        )
        invalidate_dashboard_cache()
        if status_callback:
            status_callback("Saved results and refreshed the dashboard data.")
    selected = filter_jobs(
        enriched,
        min_exp=min_exp if min_exp > 0 else None,
        max_exp=max_exp if max_exp < 40 else None,
    )
    return selected, len(raw), valid_count, dropped_count, logs.messages


def show_scrape_section() -> None:
    st.title("Scrape & Extract Data")
    st.caption(
        "Collect public job listings, enrich every record, and keep the dataset ready for analysis."
    )
    freshness = st.selectbox(
        "Jobs posted within",
        options=["Any time", "Past 12 hours", "Past 2 days", "Past 7 days", "Custom"],
        index=2,
        key="scrape_freshness",
        help="Limit results to recent postings when the source provides a posting date.",
    )
    custom_time_columns = st.columns(2)
    custom_amount = custom_time_columns[0].number_input(
        "Window",
        1.0,
        365.0,
        3.0,
        1.0,
        key="scrape_custom_amount",
        disabled=freshness != "Custom",
    )
    custom_unit = custom_time_columns[1].selectbox(
        "Unit",
        ["Days", "Hours"],
        key="scrape_custom_unit",
        disabled=freshness != "Custom",
    )
    with st.form("extraction_form", clear_on_submit=False):
        selected_roles = st.multiselect(
            "Job roles / search terms",
            ROLE_OPTIONS,
            default=[],
            key="scrape_roles",
            help="Select multiple roles to search in one extraction run.",
        )
        custom_roles = st.text_input(
            "Additional roles (optional)",
            placeholder="e.g. DevOps Engineer, BI Analyst",
            key="scrape_custom_roles",
            help="Add roles not listed above, separated by commas.",
        )
        locations = st.multiselect(
            "Locations", LOCATION_OPTIONS, default=[], key="scrape_locations"
        )
        platform_columns = st.columns(4)
        platforms: list[str] = []
        for column, label in zip(platform_columns, PLATFORM_LABELS, strict=True):
            if column.checkbox(label, value=False, key=f"scrape_{PLATFORM_LABELS[label]}"):
                platforms.append(PLATFORM_LABELS[label])
        result_options: list[int | str] = list(range(10, 201, 10)) + ["No limit"]
        result_choice = st.select_slider(
            "Max results per search",
            options=result_options,
            value=50,  # Default to 50 instead of "No limit"
            key="scrape_max_results",
            help="Drag to the far right for No limit, or choose a numeric maximum.",
        )
        max_results = None if result_choice == "No limit" else int(result_choice)
        experience_columns = st.columns(2)
        min_exp = experience_columns[0].number_input(
            "Min experience (years)", 0.0, 40.0, 0.0, 0.5, key="scrape_min_exp"
        )
        max_exp = experience_columns[1].number_input(
            "Max experience (years)", 0.0, 40.0, 40.0, 0.5, key="scrape_max_exp"
        )
        with st.expander("Proxy configuration (optional)"):
            # Use environment variable for default proxy list
            default_proxy_list = os.environ.get("PROXY_LIST", "")
            proxy_text = st.text_area(
                "Proxies",
                value=default_proxy_list,
                placeholder="host:port, user:pass@host:port",
                height=80,
                key="scrape_proxy_text",
            )
            use_public_proxies = st.checkbox(
                "Allow untrusted public proxy fallback after a block",
                value=False,
                key="scrape_public_proxies",
                help="Public proxies are disabled by default and are never combined with configured proxy credentials.",
            )
        action_columns = st.columns(2)
        reset_submitted = action_columns[0].form_submit_button("Reset selections", width="stretch")
        submitted = action_columns[1].form_submit_button(
            "Start Extraction Pipeline", type="primary", width="stretch"
        )

    if reset_submitted:
        _reset_state(
            [
                "scrape_roles",
                "scrape_custom_roles",
                "scrape_locations",
                "scrape_linkedin",
                "scrape_indeed",
                "scrape_glassdoor",
                "scrape_naukri",
                "scrape_max_results",
                "scrape_freshness",
                "scrape_custom_amount",
                "scrape_custom_unit",
                "scrape_min_exp",
                "scrape_max_exp",
                "scrape_proxy_text",
                "scrape_public_proxies",
            ]
        )

    if not submitted:
        return
    terms = selected_roles + _parse_terms(custom_roles)
    if not terms:
        st.error("Select at least one job role or add a custom search term.")
        return
    if not locations or not platforms:
        st.error("Select at least one location and platform.")
        return
    if min_exp > max_exp:
        st.error("Minimum experience cannot exceed maximum experience.")
        return

    # Estimate the run before starting so large or uncapped searches show useful timing.
    combination_count = len(terms) * len(locations) * len(platforms)
    estimated_seconds = _estimate_extraction_seconds(combination_count, max_results)
    worker_count = _search_worker_count(combination_count)
    limit_description = (
        "no result cap" if max_results is None else f"up to {max_results} results per search"
    )
    st.info(
        f"Estimated time: about {_format_duration(estimated_seconds)} for "
        f"{combination_count} searches ({limit_description}) using up to {worker_count} workers. "
        "Actual time varies with source response times, retries, and listing volume."
    )

    progress = st.progress(0, text="Preparing extraction")
    freshness_hours = {
        "Any time": None,
        "Past 12 hours": 12,
        "Past 2 days": 48,
        "Past 7 days": 168,
    }.get(freshness)
    if freshness == "Custom":
        freshness_hours = _window_hours(custom_amount, custom_unit)
    started_monotonic = time.monotonic()
    work_log: list[str] = []
    with st.status("Running extraction pipeline", expanded=True) as status:
        st.markdown("**Work log**")
        work_log_view = st.empty()

        def add_work_log(message: str) -> None:
            timestamp = datetime.now().strftime("%H:%M:%S")
            work_log.append(f"[{timestamp}] {message}")
            work_log_view.code("\n".join(work_log[-80:]))

        add_work_log(
            f"Started {combination_count} searches; estimated duration "
            f"about {_format_duration(estimated_seconds)}."
        )

        def update_progress(completed: int, total: int, label: str) -> None:
            elapsed = time.monotonic() - started_monotonic
            if completed:
                total_estimate = elapsed * total / completed + 30
                remaining = max(0, total_estimate - elapsed)
            else:
                remaining = estimated_seconds
            percent = int((completed / total) * 85) if total else 85
            progress.progress(
                percent,
                text=(
                    f"Searches {completed}/{total} · elapsed {_format_duration(elapsed)} "
                    f"· ETA {_format_duration(remaining)}"
                ),
            )
            add_work_log(f"{completed}/{total} — {label}")

        def update_stage(message: str) -> None:
            add_work_log(message)
            if message.startswith("All searches finished"):
                progress.progress(90, text="Searches finished; enriching and deduplicating")
            elif message.startswith("Saving"):
                progress.progress(95, text="Saving database and export files")
            elif message.startswith("Saved"):
                progress.progress(99, text="Saved; refreshing results")

        try:
            records, raw_count, valid_count, dropped_count, logs = run_extraction(
                terms,
                locations,
                platforms,
                max_results,
                min_exp,
                max_exp,
                _parse_proxies(proxy_text),
                hours_old=freshness_hours,
                progress_callback=update_progress,
                use_public_proxies=use_public_proxies,
                status_callback=update_stage,
            )
            progress.progress(100, text="Extraction complete")
            for message in logs:
                add_work_log(message)
            status.update(label="Extraction complete", state="complete")
            st.success(
                f"Collected {raw_count:,} raw records and saved {valid_count:,} valid listings. "
                f"{len(records):,} match the selected experience range; "
                f"dropped {dropped_count:,} rows with missing job_url."
            )
            st.info(
                "The result table is narrowed by the selected experience range. The database and "
                "exports keep all collected listings; use the Analytics Dashboard experience "
                "filters to narrow the saved dataset."
            )
            invalidate_dashboard_cache()
            visible_records = _display_frame(records)
            if len(visible_records) > 500:
                st.caption(
                    f"Showing the first 500 of {len(visible_records):,} matching rows. "
                    "All collected records are saved to the database and exports."
                )
            st.dataframe(visible_records.head(500), hide_index=True, width="stretch")
        except Exception as error:
            progress.empty()
            add_work_log(f"Extraction stopped with an error: {error}")
            status.update(label="Extraction completed with an error", state="error")
            st.warning(f"The pipeline could not complete: {error}")


def _display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [column for column in DASHBOARD_COLUMNS if column in frame.columns]
    display = frame[columns].copy().rename(columns=DASHBOARD_COLUMNS)
    if "Source" in display.columns and "sources" in frame.columns:
        display["Source"] = frame["sources"].map(lambda values: ", ".join(values))
    if "Extracted Skills" in display.columns:
        display["Extracted Skills"] = display["Extracted Skills"].map(
            lambda values: ", ".join(values)
        )
    return display


def _load_jobs_for_tailoring() -> pd.DataFrame:
    """Read descriptions only for the dedicated resume tailoring workflow."""
    if not DATABASE_PATH.exists():
        return pd.DataFrame()
    migrate_job_database(DATABASE_PATH)
    with closing(sqlite3.connect(DATABASE_PATH, timeout=30)) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        if not table:
            return pd.DataFrame()
        available = {row[1] for row in connection.execute('PRAGMA table_info("jobs")')}
        fields = [
            "title",
            "company",
            "location",
            "site",
            "job_url",
            "description_raw",
            "description_status",
            "requirements_json",
        ]
        selected = [
            f'"{field}"' if field in available else f'NULL AS "{field}"' for field in fields
        ]
        return pd.read_sql_query(
            f"SELECT rowid AS job_id, {', '.join(selected)} FROM jobs ORDER BY rowid DESC",
            connection,
        )


def show_tailor_resume() -> None:
    from jobmarket.resume import (
        ResumeLoadError,
        TailoringError,
        load_master_resume,
        match_resume,
        render_resume_docx,
        save_resume_docx,
        tailor_resume,
    )

    st.title("Tailor Resume")
    st.caption(
        "Match your master resume to a saved job, tailor relevant bullets, and download an ATS-friendly DOCX."
    )
    try:
        resume = load_master_resume()
    except ResumeLoadError as error:
        st.error(str(error))
        return
    try:
        jobs = _load_jobs_for_tailoring()
    except (OSError, sqlite3.Error, pd.errors.DatabaseError) as error:
        st.error(f"Could not load saved jobs from {DATABASE_PATH}: {error}")
        return
    if jobs.empty:
        st.info(
            "No saved jobs are available. Run a search and save jobs before tailoring a resume."
        )
        return

    def label(row: pd.Series) -> str:
        def value(key: str, fallback: str) -> str:
            item = row.get(key)
            return (
                fallback
                if item is None or pd.isna(item) or not str(item).strip()
                else str(item).strip()
            )

        parts = [value("title", "Untitled role"), value("company", "Unknown company")]
        location = value("location", "")
        if location:
            parts.append(location)
        return " · ".join(parts)

    labels = [label(row) for _, row in jobs.iterrows()]
    selected_index = st.selectbox(
        "Choose a saved job",
        range(len(jobs)),
        format_func=lambda index: labels[index],
        key="tailor_job_index",
    )
    job = jobs.iloc[selected_index]
    job_id = str(job["job_id"])
    description = str(job.get("description_raw") or "").strip()
    status = str(job.get("description_status") or "missing").casefold()
    if status != "ok":
        st.warning(
            f"This job's description status is '{status}'. Matching and tailoring may be incomplete."
        )

    if st.session_state.get("tailor_selected_job_id") != job_id:
        st.session_state["tailor_selected_job_id"] = job_id
        st.session_state.pop("tailor_result", None)
        st.session_state.pop("tailor_docx", None)
    if not description:
        st.info(
            "This saved job has no description text to match against. Add a description before tailoring."
        )
        return

    raw_requirements = job.get("requirements_json")
    try:
        requirements = (
            json.loads(raw_requirements)
            if isinstance(raw_requirements, str) and raw_requirements.strip()
            else extract_requirements(description)
        )
        if not isinstance(requirements, dict):
            requirements = extract_requirements(description)
    except (json.JSONDecodeError, TypeError, ValueError):
        requirements = extract_requirements(description)
    match = match_resume(resume, requirements, top_n=12)
    st.metric("Resume match", f"{match['match_score']} / 100")
    skill_columns = st.columns(3)
    skill_columns[0].markdown("**Matched skills**")
    skill_columns[0].write(", ".join(match["matched_skills"]) or "None identified")
    skill_columns[1].markdown("**Missing required skills**")
    skill_columns[1].write(", ".join(match["missing_required_skills"]) or "None identified")
    skill_columns[2].markdown("**Missing preferred skills**")
    skill_columns[2].write(", ".join(match["missing_preferred_skills"]) or "None identified")

    if st.button("Tailor selected bullets", type="primary", key=f"tailor_generate_{job_id}"):
        try:
            tailored = tailor_resume(resume, description, match)
            st.session_state["tailor_result"] = tailored.model_dump()
            st.session_state.pop("tailor_docx", None)
        except TailoringError as error:
            st.error(str(error))
            return

    result = st.session_state.get("tailor_result")
    if not result:
        st.info("Select Tailor selected bullets to generate editable suggestions.")
        return

    if result.get("gaps"):
        with st.container(border=True):
            st.subheader("Gaps")
            for gap in result["gaps"]:
                st.write(f"• {gap}")
    else:
        st.success("No unmet required skills were returned by the matcher.")

    original_by_id = {
        bullet.id: bullet.text for entry in resume.experience for bullet in entry.bullets
    }
    original_by_id.update(
        {bullet.id: bullet.text for project in resume.projects for bullet in project.bullets}
    )
    source_label = {}
    for entry in resume.experience:
        for bullet in entry.bullets:
            source_label[bullet.id] = f"{entry.role} · {entry.company}"
    for project in resume.projects:
        for bullet in project.bullets:
            source_label[bullet.id] = f"Project · {project.name}"

    selected_bullets = result.get("selected_bullets", [])
    if selected_bullets:
        st.subheader("Original and tailored bullets")
        header = st.columns(2)
        header[0].markdown("**Original**")
        header[1].markdown("**Tailored (editable)**")
        edits: dict[str, str] = {}
        for index, bullet in enumerate(selected_bullets):
            source_id = str(bullet["source_id"])
            columns = st.columns(2)
            with columns[0]:
                st.caption(source_label.get(source_id, "Resume bullet"))
                st.write(original_by_id.get(source_id, ""))
            with columns[1]:
                edits[source_id] = st.text_area(
                    "Tailored text",
                    value=bullet["text"],
                    key=f"tailor_edit_{job_id}_{index}",
                    label_visibility="collapsed",
                    height=110,
                )
        if st.button("Save DOCX and prepare download", key=f"tailor_save_{job_id}"):
            try:
                docx_data = render_resume_docx(resume, match_result=match, tailored_bullets=edits)
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                output_name = f"job_{job_id}_{timestamp}.docx"
                output_path = save_resume_docx(docx_data, BASE_DIR / "outputs" / output_name)
                st.session_state["tailor_docx"] = {
                    "data": docx_data,
                    "name": output_name,
                    "path": str(output_path),
                }
            except (OSError, ValueError) as error:
                st.error(f"Could not render the resume: {error}")
    else:
        st.info("The matcher did not find resume bullets with relevant overlap to rewrite.")

    saved_docx = st.session_state.get("tailor_docx")
    if saved_docx:
        st.caption(f"Saved generated resume to {saved_docx['path']}")
        st.download_button(
            "Download tailored resume (.docx)",
            saved_docx["data"],
            saved_docx["name"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key=f"tailor_download_{job_id}",
        )


def show_dashboard() -> None:
    st.title("Analytics Dashboard")
    csv_files = sorted(BASE_DIR.glob("*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    source_options = ["SQLite database (jobs.db)"] + [f"CSV: {path.name}" for path in csv_files]
    selected_source = st.sidebar.selectbox(
        "Data source", source_options, key="dashboard_data_source"
    )
    if st.sidebar.button("Refresh data", key="refresh_dashboard_data"):
        invalidate_dashboard_cache()
        st.rerun()
    if selected_source.startswith("CSV: "):
        selected_path = BASE_DIR / selected_source.removeprefix("CSV: ")
        try:
            stat = selected_path.stat()
            data = load_csv_file(str(selected_path), f"{stat.st_mtime_ns}:{stat.st_size}")
        except CSVImportError as error:
            st.error(f"Could not import {selected_path.name}: {error}")
            st.stop()
        except Exception:
            logging.getLogger(__name__).exception(
                "Unexpected error importing Analytics CSV %s", selected_path
            )
            st.error(
                f"Could not process {selected_path.name}. Check that it contains readable job data, then try again."
            )
            st.stop()
    else:
        data = load_jobs(
            str(DATABASE_PATH),
            str(BASE_DIR),
            _dashboard_cache_version(str(DATABASE_PATH), str(BASE_DIR)),
        )
    if data.empty:
        st.info("No extracted jobs found. Run the extraction pipeline first.")
        return

    st.sidebar.subheader("Dashboard filters")
    source_options = sorted({source for source_list in data["sources"] for source in source_list})
    sources = st.sidebar.multiselect("Source", source_options, key="dashboard_source")
    roles = sorted(value for value in data["search_term"].unique() if value)
    selected_roles = st.sidebar.multiselect("Job role", roles, key="dashboard_role")
    qualifications = st.sidebar.multiselect(
        "Qualification", sorted(data["qualification"].unique()), key="dashboard_qualification"
    )
    skill_values = sorted(
        {
            skill
            for skills in data["extracted_skills"]
            for skill in skills
            if skill != "Extracted from Title Only"
        }
    )
    skills = st.sidebar.multiselect("Skills", skill_values, key="dashboard_skills")
    seniorities = st.sidebar.multiselect(
        "Seniority bucket",
        ["Entry-Level", "Mid-Level", "Senior/Lead"],
        default=[],
        key="dashboard_seniority",
    )
    experience_columns = st.sidebar.columns(2)
    dashboard_min_exp = experience_columns[0].number_input(
        "Min experience (years)", 0.0, 40.0, 0.0, 0.5, key="dashboard_min_exp"
    )
    dashboard_max_exp = experience_columns[1].number_input(
        "Max experience (years)", 0.0, 40.0, 40.0, 0.5, key="dashboard_max_exp"
    )
    locations = st.sidebar.multiselect(
        "Location",
        sorted(value for value in data["location"].unique() if value),
        key="dashboard_location",
    )
    work_modes = st.sidebar.multiselect(
        "Work mode",
        sorted(value for value in data["work_mode"].unique() if value),
        key="dashboard_work_mode",
    )
    minimum_quality = st.sidebar.slider(
        "Minimum data quality", 0, 100, 0, 5, key="dashboard_quality"
    )

    posted_values = data["date_posted"].dropna()
    posted_filter = st.sidebar.selectbox(
        "Posted time filter",
        [
            "Any time",
            "Past 12 hours",
            "Past 2 days",
            "Past 7 days",
            "Custom window",
            "Custom date range",
        ],
        index=0,
        key="dashboard_posted_filter",
    )
    dashboard_hours = {"Past 12 hours": 12, "Past 2 days": 48, "Past 7 days": 168}.get(
        posted_filter
    )
    dashboard_amount = 3.0
    dashboard_unit = "Days"
    if posted_filter == "Custom window":
        dashboard_time_columns = st.sidebar.columns(2)
        dashboard_amount = dashboard_time_columns[0].number_input(
            "Window", 1.0, 365.0, 3.0, 1.0, key="dashboard_window"
        )
        dashboard_unit = dashboard_time_columns[1].selectbox(
            "Unit", ["Days", "Hours"], key="dashboard_unit"
        )
        dashboard_hours = _window_hours(dashboard_amount, dashboard_unit)
    posted_range = None
    if posted_filter == "Custom date range" and not posted_values.empty:
        posted_range = st.sidebar.date_input(
            "Posted date range",
            value=(posted_values.min().date(), posted_values.max().date()),
            min_value=posted_values.min().date(),
            max_value=posted_values.max().date(),
            key="dashboard_posted_range",
        )
    if st.sidebar.button(
        "Reset dashboard selections",
        key="reset_dashboard",
        type="secondary",
        width="stretch",
    ):
        _reset_state(
            [
                "dashboard_data_source",
                "dashboard_source",
                "dashboard_role",
                "dashboard_qualification",
                "dashboard_skills",
                "dashboard_seniority",
                "dashboard_min_exp",
                "dashboard_max_exp",
                "dashboard_location",
                "dashboard_work_mode",
                "dashboard_quality",
                "dashboard_posted_filter",
                "dashboard_window",
                "dashboard_unit",
                "dashboard_posted_range",
            ]
        )

    filtered = data.copy()
    if sources:
        filtered = filtered[
            filtered["sources"].map(lambda values: any(source in values for source in sources))
        ]
    if selected_roles:
        filtered = filtered[filtered["search_term"].isin(selected_roles)]
    if qualifications:
        filtered = filtered[filtered["qualification"].isin(qualifications)]
    if skills:
        filtered = filtered[
            filtered["extracted_skills"].map(
                lambda values: all(skill in values for skill in skills)
            )
        ]
    if seniorities:
        filtered = filtered[filtered["seniority"].isin(seniorities)]
    if locations:
        filtered = filtered[filtered["location"].isin(locations)]
    if work_modes:
        filtered = filtered[filtered["work_mode"].isin(work_modes)]
    if dashboard_min_exp > dashboard_max_exp:
        st.error("Minimum experience cannot exceed maximum experience.")
        return
    if dashboard_min_exp > 0 or dashboard_max_exp < 40:
        filtered = filter_jobs(
            filtered,
            min_exp=dashboard_min_exp if dashboard_min_exp > 0 else None,
            max_exp=dashboard_max_exp if dashboard_max_exp < 40 else None,
        )
    filtered = filtered[filtered["data_quality_score"] >= minimum_quality]
    if dashboard_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=dashboard_hours)
        filtered = filtered[filtered["date_posted"].ge(cutoff) | filtered["date_posted"].isna()]
    if posted_range and len(posted_range) == 2:
        start_date, end_date = posted_range
        posted_dates = filtered["date_posted"].dt.date
        filtered = filtered[
            posted_dates.between(start_date, end_date) | filtered["date_posted"].isna()
        ]

    metric_columns = st.columns(4)
    skill_counts = _skill_counts(filtered)
    qualification_counts = filtered["qualification"].value_counts()
    metric_columns[0].metric("Total active jobs", f"{len(filtered):,}")
    metric_columns[1].metric(
        "Top demanded skill", skill_counts.index[0] if not skill_counts.empty else "None"
    )
    metric_columns[2].metric(
        "Most common qualification",
        qualification_counts.index[0] if not qualification_counts.empty else "None",
    )
    average_exp = filtered["min_exp"].mean()
    metric_columns[3].metric(
        "Average min experience",
        "Not specified" if pd.isna(average_exp) else f"{average_exp:.1f} years",
    )

    chart_columns = st.columns(2)
    with chart_columns[0]:
        qualification_chart = qualification_counts.rename_axis("Qualification").reset_index(
            name="Jobs"
        )
        st.plotly_chart(
            px.bar(
                qualification_chart,
                x="Qualification",
                y="Jobs",
                title="Exact qualification breakdown",
            ),
            width="stretch",
        )
    with chart_columns[1]:
        skill_chart = (
            skill_counts.head(10).sort_values().rename_axis("Skill").reset_index(name="Jobs")
        )
        st.plotly_chart(
            px.bar(skill_chart, x="Jobs", y="Skill", orientation="h", title="Top skills demand"),
            width="stretch",
        )

    matrix = filtered.dropna(subset=["min_exp"]).copy()
    if not matrix.empty:
        seniority_order = ["Entry-Level", "Mid-Level", "Senior/Lead", "Not Specified"]
        figure = go.Figure()
        for bucket in seniority_order:
            bucket_frame = matrix[matrix["seniority"].eq(bucket)]
            if bucket_frame.empty:
                continue
            figure.add_trace(
                go.Box(
                    x=bucket_frame["min_exp"],
                    y=[bucket] * len(bucket_frame),
                    name=bucket,
                    orientation="h",
                    boxpoints="all",
                    jitter=0.35,
                    pointpos=0,
                    marker={"size": 5, "opacity": 0.55},
                    line={"width": 1.5},
                    customdata=bucket_frame[["title", "company"]].fillna("").to_numpy(),
                    hovertemplate="%{customdata[0]}<br>%{customdata[1]}<br>Min experience: %{x} years<extra></extra>",
                )
            )
        figure.update_layout(
            title="Experience vs seniority: box plot with jittered job points",
            xaxis_title="Minimum experience (years)",
            yaxis_title="Seniority",
            showlegend=False,
            boxmode="group",
        )
        st.plotly_chart(figure, width="stretch")
    else:
        st.info("No numeric experience values are available for the current selection.")

    st.subheader("Filtered job records")
    display = _display_frame(filtered)
    if len(display) > 500:
        st.caption(
            f"Showing the first 500 of {len(display):,} matching rows. "
            "The download contains all matching rows."
        )
    if "Apply URL" in display.columns:
        st.dataframe(
            display.head(500),
            column_config={"Apply URL": st.column_config.LinkColumn("Apply URL")},
            hide_index=True,
            width="stretch",
        )
    else:
        st.dataframe(display.head(500), hide_index=True, width="stretch")
    csv_data = display.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download filtered CSV", csv_data, "filtered_jobs.csv", "text/csv", width="stretch"
    )

    runs = load_extraction_runs(str(DATABASE_PATH))
    if not runs.empty:
        with st.expander("Recent extraction health"):
            health = runs[["started_at", "raw_count", "valid_count", "status"]].copy()
            health.columns = ["Started", "Raw Records", "Valid Records", "Status"]
            st.dataframe(health, hide_index=True, width="stretch")


def _skill_counts(frame: pd.DataFrame) -> pd.Series:
    values = [
        skill
        for skills in frame["extracted_skills"]
        for skill in skills
        if skill != "Extracted from Title Only"
    ]
    return pd.Series(values, dtype="string").value_counts() if values else pd.Series(dtype="int64")


def main() -> None:
    st.set_page_config(page_title="Job Market Explorer", page_icon="J", layout="wide")
    st.markdown(
        """
        <style>
        [data-testid="stMetric"] { border-left: 3px solid #0f766e; padding-left: 1rem; }
        </style>
    """,
        unsafe_allow_html=True,
    )
    section = st.sidebar.radio(
        "Application", ["Scrape & Extract Data", "Analytics Dashboard", "Tailor Resume"]
    )
    if section == "Scrape & Extract Data":
        show_scrape_section()
    elif section == "Analytics Dashboard":
        show_dashboard()
    else:
        show_tailor_resume()


if __name__ == "__main__":
    main()

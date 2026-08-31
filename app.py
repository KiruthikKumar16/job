"""Interactive Streamlit UI for the job scraping and analytics pipeline."""
from __future__ import annotations

import ast
import io
import json
import logging
import sqlite3
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4

import pandas as pd
import plotly.express as px
import streamlit as st

from filter import filter_jobs
from parser import enrich_jobs
from scraper import INDIA_PLATFORMS, NORMALIZED_COLUMNS, fetch_jobs
from storage import load_extraction_runs, save_extraction_run, save_to_files, save_to_sqlite

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "jobs.db"
PLATFORM_LABELS = {
    "LinkedIn": "linkedin",
    "Indeed": "indeed",
    "Glassdoor": "glassdoor",
    "Naukri": "naukri",
}
LOCATION_OPTIONS = ["Bengaluru", "Hyderabad", "Pune", "Mumbai", "Chennai", "Delhi NCR"]
ROLE_OPTIONS = [
    "Data Analyst", "Data Engineer", "Full Stack Developer", "Backend Developer",
    "Frontend Developer", "Python Developer", "Machine Learning Engineer", "Cloud Engineer",
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


@st.cache_data(ttl=30, show_spinner=False)
def load_jobs(database_path: str, data_directory: str) -> pd.DataFrame:
    """Load the SQLite dataset, falling back to the newest portable export."""
    database = Path(database_path)
    if database.exists():
        try:
            with sqlite3.connect(database) as connection:
                tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", connection)
                if "jobs" in tables["name"].tolist():
                    return _prepare_frame(pd.read_sql_query("SELECT * FROM jobs", connection))
        except (OSError, sqlite3.Error, pd.errors.DatabaseError):
            pass

    directory = Path(data_directory)
    exports = sorted(directory.glob("jobs_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if exports:
        return _prepare_frame(pd.read_csv(exports[0]))
    json_exports = sorted(directory.glob("jobs_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if json_exports:
        return _prepare_frame(pd.read_json(json_exports[0]))
    return pd.DataFrame()


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ("site", "search_term", "title", "company", "location", "qualification", "seniority", "job_url", "work_mode"):
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
    if "data_quality_score" not in result.columns:
        result["data_quality_score"] = 0
    result["data_quality_score"] = pd.to_numeric(result["data_quality_score"], errors="coerce").fillna(0)
    if "date_posted" not in result.columns:
        result["date_posted"] = pd.NaT
    result["date_posted"] = pd.to_datetime(result["date_posted"], errors="coerce", utc=True)
    result["qualification"] = result["qualification"].replace({"": "Degree Required"})
    return result


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


def run_extraction(terms: list[str], locations: list[str], platforms: list[str], max_results: int,
                   min_exp: float, max_exp: float, proxies: list[str] | None,
                   hours_old: int | None = None,
                   progress_callback: Callable[[int, int, str], None] | None = None
                   ) -> tuple[pd.DataFrame, int, int, list[str]]:
    """Fetch each query independently so blocked sources cannot stall the UI."""
    combinations = [(term, location, platform) for term in terms for location in locations for platform in platforms]
    started_at = datetime.now(timezone.utc)
    raw_frames: list[pd.DataFrame] = []
    platform_status: dict[str, str] = {}
    with capture_pipeline_logs() as logs:
        def collect_one(combination: tuple[str, str, str]) -> tuple[tuple[str, str, str], pd.DataFrame | None, str]:
            term, location, platform = combination
            try:
                frame = fetch_jobs(
                    [term], [location], [platform], max_results=max_results,
                    proxies=proxies, hours_old=hours_old,
                )
                return combination, frame, "success" if not frame.empty else "empty"
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Continuing after %s failed for %s at %s: %s", platform.title(), term, location, error,
                )
                return combination, None, f"failed: {error}"

        worker_count = min(6, max(1, len(combinations)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(collect_one, combination) for combination in combinations]
            for index, future in enumerate(as_completed(futures), start=1):
                (term, location, platform), frame, status = future.result()
                platform_status[f"{platform}:{location}:{term}"] = status
                if frame is not None and not frame.empty:
                    raw_frames.append(frame)
                if progress_callback:
                    progress_callback(index, len(combinations), f"Finished {platform.title()} for {location}")
        raw = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame(columns=NORMALIZED_COLUMNS)
        if not raw.empty:
            raw = raw.drop_duplicates(subset=["job_url"], keep="first").reset_index(drop=True)
        enriched = enrich_jobs(raw)
        save_to_sqlite(enriched, str(DATABASE_PATH), "jobs")
        save_to_files(enriched, str(BASE_DIR / "jobs"))
        valid_count = int(enriched.get("title", pd.Series(dtype=str)).astype(str).str.strip().ne("").sum())
        save_extraction_run(str(DATABASE_PATH), {
            "run_id": str(uuid4()),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "raw_count": len(raw),
            "valid_count": valid_count,
            "status": "completed" if all(value in {"success", "empty"} for value in platform_status.values()) else "partial",
            "platform_status": platform_status,
        })
    selected = filter_jobs(enriched, min_exp=min_exp, max_exp=max_exp)
    return selected, len(raw), valid_count, logs.messages


def show_scrape_section() -> None:
    st.title("Scrape & Extract Data")
    st.caption("Collect public job listings, enrich every record, and keep the dataset ready for analysis.")
    with st.form("extraction_form", clear_on_submit=False):
        selected_roles = st.multiselect(
            "Job roles / search terms",
            ROLE_OPTIONS,
            default=["Data Analyst", "Data Engineer", "Full Stack Developer"],
            help="Select multiple roles to search in one extraction run.",
        )
        custom_roles = st.text_input(
            "Additional roles (optional)",
            placeholder="e.g. DevOps Engineer, BI Analyst",
            help="Add roles not listed above, separated by commas.",
        )
        locations = st.multiselect("Locations", LOCATION_OPTIONS, default=["Bengaluru", "Hyderabad"])
        platform_columns = st.columns(4)
        platforms: list[str] = []
        for column, label in zip(platform_columns, PLATFORM_LABELS, strict=True):
            if column.checkbox(label, value=True):
                platforms.append(PLATFORM_LABELS[label])
        max_results = st.slider("Max results per search", 10, 200, 50, step=10)
        freshness = st.selectbox(
            "Jobs posted within",
            options=["Any time", "Past 12 hours", "Past 2 days", "Past 7 days"],
            index=2,
            help="Limit results to recent postings when the source provides a posting date.",
        )
        experience_columns = st.columns(2)
        min_exp = experience_columns[0].number_input("Min experience (years)", 0.0, 40.0, 0.0, 0.5)
        max_exp = experience_columns[1].number_input("Max experience (years)", 0.0, 40.0, 40.0, 0.5)
        with st.expander("Proxy configuration (optional)"):
            proxy_text = st.text_area("Proxies", placeholder="host:port, user:pass@host:port", height=80)
        submitted = st.form_submit_button("Start Extraction Pipeline", type="primary", use_container_width=True)

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

    progress = st.progress(0, text="Starting extraction pipeline")
    freshness_hours = {"Any time": None, "Past 12 hours": 12, "Past 2 days": 48, "Past 7 days": 168}[freshness]
    with st.status("Running extraction pipeline", expanded=True) as status:
        try:
            progress.progress(20, text="Collecting public listings")
            records, raw_count, valid_count, logs = run_extraction(
                terms, locations, platforms, max_results, min_exp, max_exp,
                _parse_proxies(proxy_text),
                hours_old=freshness_hours,
                progress_callback=lambda completed, total, label: progress.progress(
                    int((completed / total) * 90) if total else 90, text=label,
                ),
            )
            progress.progress(100, text="Extraction complete")
            for message in logs:
                st.write(message)
            status.update(label="Extraction complete", state="complete")
            st.success(f"Scraped {raw_count:,} raw records and saved {valid_count:,} valid extracted records.")
            st.info("The SQLite database and timestamped jobs CSV/JSON exports have been updated.")
            load_jobs.clear()
            st.dataframe(_display_frame(records), hide_index=True, use_container_width=True)
        except Exception as error:
            progress.empty()
            status.update(label="Extraction completed with an error", state="error")
            st.warning(f"The pipeline could not complete: {error}")


def _display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [column for column in DASHBOARD_COLUMNS if column in frame.columns]
    display = frame[columns].copy().rename(columns=DASHBOARD_COLUMNS)
    if "Extracted Skills" in display.columns:
        display["Extracted Skills"] = display["Extracted Skills"].map(lambda values: ", ".join(values))
    return display


def show_dashboard() -> None:
    st.title("Analytics Dashboard")
    data = load_jobs(str(DATABASE_PATH), str(BASE_DIR))
    if data.empty:
        st.info("No extracted jobs found. Run the extraction pipeline first.")
        return

    st.sidebar.subheader("Dashboard filters")
    sources = st.sidebar.multiselect("Source", sorted(data["site"].unique()))
    roles = sorted(value for value in data["search_term"].unique() if value)
    selected_roles = st.sidebar.multiselect("Job role", roles)
    qualifications = st.sidebar.multiselect("Qualification", sorted(data["qualification"].unique()))
    skill_values = sorted({skill for skills in data["extracted_skills"] for skill in skills if skill != "Extracted from Title Only"})
    skills = st.sidebar.multiselect("Skills", skill_values)
    seniorities = st.sidebar.multiselect("Seniority bucket", ["Entry-Level", "Mid-Level", "Senior/Lead"], default=[])
    locations = st.sidebar.multiselect("Location", sorted(value for value in data["location"].unique() if value))
    work_modes = st.sidebar.multiselect("Work mode", sorted(value for value in data["work_mode"].unique() if value))
    minimum_quality = st.sidebar.slider("Minimum data quality", 0, 100, 0, 5)

    posted_values = data["date_posted"].dropna()
    posted_range = None
    if not posted_values.empty:
        posted_range = st.sidebar.date_input(
            "Posted date range", value=(posted_values.min().date(), posted_values.max().date()),
            min_value=posted_values.min().date(), max_value=posted_values.max().date(),
        )

    filtered = data.copy()
    if sources:
        filtered = filtered[filtered["site"].isin(sources)]
    if selected_roles:
        filtered = filtered[filtered["search_term"].isin(selected_roles)]
    if qualifications:
        filtered = filtered[filtered["qualification"].isin(qualifications)]
    if skills:
        filtered = filtered[filtered["extracted_skills"].map(lambda values: all(skill in values for skill in skills))]
    if seniorities:
        filtered = filtered[filtered["seniority"].isin(seniorities)]
    if locations:
        filtered = filtered[filtered["location"].isin(locations)]
    if work_modes:
        filtered = filtered[filtered["work_mode"].isin(work_modes)]
    filtered = filtered[filtered["data_quality_score"] >= minimum_quality]
    if posted_range and len(posted_range) == 2:
        start_date, end_date = posted_range
        posted_dates = filtered["date_posted"].dt.date
        filtered = filtered[posted_dates.between(start_date, end_date) | filtered["date_posted"].isna()]

    metric_columns = st.columns(4)
    skill_counts = _skill_counts(filtered)
    qualification_counts = filtered["qualification"].value_counts()
    metric_columns[0].metric("Total active jobs", f"{len(filtered):,}")
    metric_columns[1].metric("Top demanded skill", skill_counts.index[0] if not skill_counts.empty else "None")
    metric_columns[2].metric("Most common qualification", qualification_counts.index[0] if not qualification_counts.empty else "None")
    average_exp = filtered["min_exp"].mean()
    metric_columns[3].metric("Average min experience", "Not specified" if pd.isna(average_exp) else f"{average_exp:.1f} years")

    chart_columns = st.columns(2)
    with chart_columns[0]:
        qualification_chart = qualification_counts.rename_axis("Qualification").reset_index(name="Jobs")
        st.plotly_chart(px.bar(qualification_chart, x="Qualification", y="Jobs", title="Exact qualification breakdown"), use_container_width=True)
    with chart_columns[1]:
        skill_chart = skill_counts.head(10).sort_values().rename_axis("Skill").reset_index(name="Jobs")
        st.plotly_chart(px.bar(skill_chart, x="Jobs", y="Skill", orientation="h", title="Top skills demand"), use_container_width=True)

    matrix = filtered.dropna(subset=["min_exp"]).copy()
    if not matrix.empty:
        st.plotly_chart(px.scatter(matrix, x="min_exp", y="seniority", hover_name="title", color="seniority", title="Experience vs seniority"), use_container_width=True)
    else:
        st.info("No numeric experience values are available for the current selection.")

    st.subheader("Filtered job records")
    display = _display_frame(filtered)
    if "Apply URL" in display.columns:
        st.dataframe(display, column_config={"Apply URL": st.column_config.LinkColumn("Apply URL")}, hide_index=True, use_container_width=True)
    else:
        st.dataframe(display, hide_index=True, use_container_width=True)
    csv_data = display.to_csv(index=False).encode("utf-8")
    st.download_button("Download filtered CSV", csv_data, "filtered_jobs.csv", "text/csv", use_container_width=True)

    runs = load_extraction_runs(str(DATABASE_PATH))
    if not runs.empty:
        with st.expander("Recent extraction health"):
            health = runs[["started_at", "raw_count", "valid_count", "status"]].copy()
            health.columns = ["Started", "Raw Records", "Valid Records", "Status"]
            st.dataframe(health, hide_index=True, use_container_width=True)


def _skill_counts(frame: pd.DataFrame) -> pd.Series:
    values = [skill for skills in frame["extracted_skills"] for skill in skills if skill != "Extracted from Title Only"]
    return pd.Series(values, dtype="string").value_counts() if values else pd.Series(dtype="int64")


def main() -> None:
    st.set_page_config(page_title="Job Market Explorer", page_icon="J", layout="wide")
    st.markdown("""
        <style>
        [data-testid="stMetric"] { border-left: 3px solid #0f766e; padding-left: 1rem; }
        </style>
    """, unsafe_allow_html=True)
    section = st.sidebar.radio("Application", ["Scrape & Extract Data", "Analytics Dashboard"])
    if section == "Scrape & Extract Data":
        show_scrape_section()
    else:
        show_dashboard()


if __name__ == "__main__":
    main()

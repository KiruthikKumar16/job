"""Command-line orchestration for the job extraction pipeline."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd

from jobmarket.job_filters import filter_jobs
from jobmarket.job_parser import enrich_jobs
from jobmarket.scraper import INDIA_PLATFORMS, fetch_jobs
from jobmarket.storage import save_extraction_run, save_to_files, save_to_sqlite


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _csv_values(values: list[str]) -> list[str]:
    return [item for value in values for item in _csv_list(value)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract, enrich, filter, and export public job listings."
    )
    parser.add_argument(
        "--terms",
        type=_csv_list,
        default=["software engineer"],
        help="Comma-separated search terms",
    )
    parser.add_argument(
        "--locations",
        nargs="+",
        type=str,
        default=["Bengaluru", "Hyderabad"],
        help="One or more locations, comma-separated or space-separated",
    )
    parser.add_argument(
        "--platforms",
        type=_csv_list,
        default=list(INDIA_PLATFORMS),
        help="Comma-separated platform names; India defaults exclude US/Canada-only ZipRecruiter",
    )
    parser.add_argument(
        "--country", default="India", help="Country for Indeed and Glassdoor (default: India)"
    )
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument(
        "--hours-old", type=int, help="Only include jobs posted within the last N hours (optional)"
    )
    # Use environment variable for default proxy list
    default_proxies = os.environ.get("PROXY_LIST", "")
    parser.add_argument(
        "--user-proxies",
        "--proxies",
        dest="proxies",
        nargs="+",
        type=str,
        default=default_proxies.split() if default_proxies else [],
        help="Optional explicitly configured proxy URLs",
    )
    parser.add_argument(
        "--use-public-proxies",
        action="store_true",
        help="Opt in to untrusted public proxies as a fallback after a block (disabled by default)",
    )
    parser.add_argument(
        "--skills", nargs="+", type=str, help="Required skills, comma-separated or space-separated"
    )
    parser.add_argument(
        "--degree",
        nargs="+",
        type=str,
        help="One or more accepted normalized qualification labels (space or comma-separated)",
    )
    parser.add_argument(
        "--min-exp", type=float, help="Minimum experience; includes title-classified senior roles"
    )
    parser.add_argument(
        "--max-exp",
        type=float,
        help="Maximum experience; includes title-classified entry-level roles",
    )
    parser.add_argument(
        "--seniority", choices=["Entry-Level", "Mid-Level", "Senior/Lead", "Not Specified"]
    )
    # Use environment variable for default database path
    default_db_path = os.environ.get("JOB_DATABASE_PATH", "jobs.db")
    parser.add_argument("--db", default=default_db_path)
    parser.add_argument("--table", default="jobs")
    parser.add_argument("--output", default="job_export")
    return parser


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = build_parser()
    args = parser.parse_args()
    args.locations = _csv_values(args.locations)
    if args.skills:
        args.skills = _csv_values(args.skills)
    if args.degree:
        args.degree = _csv_values(args.degree)
        # Validate degrees
        valid_degrees = [
            "B.Tech",
            "B.E.",
            "M.Tech",
            "M.E.",
            "B.Sc",
            "M.Sc",
            "BCA",
            "MCA",
            "BS",
            "MS",
            "Bachelor's",
            "Master's",
            "Diploma",
            "Degree Required",
        ]
        for degree in args.degree:
            if degree not in valid_degrees:
                parser.error(
                    f"Invalid degree: {degree}. Valid options are: {', '.join(valid_degrees)}"
                )
    if args.proxies:
        args.proxies = _csv_values(args.proxies)
    started_at = datetime.now(timezone.utc)
    try:
        raw = fetch_jobs(
            args.terms,
            args.locations,
            args.platforms,
            args.max_results,
            args.proxies,
            args.country,
            hours_old=args.hours_old,
            use_public_proxies=args.use_public_proxies,
        )
        platform_status = raw.attrs.get("platform_status", {"cli": "success"})
        logging.info("Scraped %d records", len(raw))
        enriched = enrich_jobs(raw)
        selected = filter_jobs(
            enriched,
            max_exp=args.max_exp,
            min_exp=args.min_exp,
            seniority=args.seniority,
            required_skills=args.skills,
            degree=args.degree,
        )
        logging.info("%d records remain after filtering", len(selected))
        save_to_sqlite(selected, args.db, args.table)
        json_path, csv_path = save_to_files(selected, args.output)
        logging.info("Saved SQLite database plus %s and %s", json_path, csv_path)
        # Record extraction run for dashboard health
        finished_at = datetime.now(timezone.utc)
        raw_count = len(raw)
        valid_count = int(
            enriched.get("title", pd.Series(dtype=str)).astype(str).str.strip().ne("").sum()
        )
        status = (
            "partial"
            if any(value.startswith("failed:") for value in platform_status.values())
            else "completed"
        )
        save_extraction_run(
            args.db,
            {
                "run_id": str(uuid4()),
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "raw_count": raw_count,
                "valid_count": valid_count,
                "status": status,
                "platform_status": platform_status,
            },
        )
        return 0
    except Exception as error:
        logging.exception("Pipeline failed: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

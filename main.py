"""Command-line orchestration for the job extraction pipeline."""
from __future__ import annotations

import argparse
import logging

from filter import filter_jobs
from parser import enrich_jobs
from scraper import INDIA_PLATFORMS, fetch_jobs
from storage import save_to_files, save_to_sqlite


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _csv_values(values: list[str]) -> list[str]:
    return [item for value in values for item in _csv_list(value)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract, enrich, filter, and export public job listings.")
    parser.add_argument("--terms", type=_csv_list, default=["software engineer"], help="Comma-separated search terms")
    parser.add_argument("--locations", nargs="+", type=str, default=["Bengaluru", "Hyderabad"], help="One or more locations, comma-separated or space-separated")
    parser.add_argument("--platforms", type=_csv_list, default=list(INDIA_PLATFORMS), help="Comma-separated platform names; India defaults exclude US/Canada-only ZipRecruiter")
    parser.add_argument("--country", default="India", help="Country for Indeed and Glassdoor (default: India)")
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--user-proxies", "--proxies", dest="proxies", type=_csv_list,
                        help="Optional comma-separated proxy URLs; uses an automatic pool after 403/429 when omitted")
    parser.add_argument("--skills", nargs="+", type=str, help="Required skills, comma-separated or space-separated")
    parser.add_argument("--degree", nargs="+", choices=[
        "B.Tech", "B.E.", "M.Tech", "M.E.", "B.Sc", "M.Sc", "BCA", "MCA", "BS", "MS",
        "Bachelor's", "Master's", "Diploma", "Degree Required",
    ], help="One or more accepted normalized qualification labels")
    parser.add_argument("--min-exp", type=float, help="Minimum experience; includes title-classified senior roles")
    parser.add_argument("--max-exp", type=float, help="Maximum experience; includes title-classified entry-level roles")
    parser.add_argument("--seniority", choices=["Entry-Level", "Mid-Level", "Senior/Lead", "Not Specified"])
    parser.add_argument("--db", default="jobs.db")
    parser.add_argument("--table", default="jobs")
    parser.add_argument("--output", default="job_export")
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()
    args.locations = _csv_values(args.locations)
    if args.skills:
        args.skills = _csv_values(args.skills)
    try:
        raw = fetch_jobs(args.terms, args.locations, args.platforms, args.max_results, args.proxies, args.country)
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
        return 0
    except Exception as error:
        logging.exception("Pipeline failed: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

# Changelog

All notable changes to the Job Market Scraper project are documented here.

## [Unreleased]

### Added
- **Per-site rate limiting** (`scraper.py`): configurable minimum request interval via `JOB_SCRAPER_MIN_REQUEST_INTERVAL` env var (default 1.0 s). Applied to initial fetch, proxy retries, and Playwright fallback.
- **Proxy pool caching with TTL** (`proxy_manager.py`): module-level cache with thread-safe updates; TTL configurable via `PROXY_POOL_TTL` env var (default 300 s).
- **Expanded skill catalog** (`parser.py`, `skill_catalog.json`): loaded from JSON at runtime; includes analyst/BI tools (Power BI, Tableau, Qlik, Looker, etc.), additional languages, frameworks, cloud platforms, and databases.
- **CLI `--hours-old` flag** (`main.py`): filter jobs by posting freshness.
- **CLI extraction-run recording** (`main.py`): `storage.save_extraction_run()` called after every CLI run so results appear in the Streamlit "Recent extraction health" panel.
- **Environment variable support** (`main.py`, `app.py`): `JOB_DATABASE_PATH`, `JOB_MAX_WORKERS`, `PROXY_LIST`.
- **Filter: keep unclassified experience rows** (`filter.py`): rows with `NaN` experience and `seniority == "Not Specified"` are no longer dropped when experience filters are active.

### Changed
- **Thread-safe Naukri session** (`scraper.py`): replaced global `requests.Session()` with `threading.local()` for per-thread connection pooling.
- **SQLite column types** (`storage.py`): numeric columns stored as `REAL`; automatic migration from old all-TEXT schema.
- **CLI `--degree` argument** (`main.py`): now accepts space- or comma-separated values with validation.

### Fixed
- **Browser-card metadata annotation** (`scraper.py`): `card_summary` added to `NORMALIZED_COLUMNS`; `description = None` for browser cards to prevent card-text misuse.
- **Export filename constant** (`storage.py`): `EXPORT_BASE_FILENAME` used consistently; `save_to_sqlite` logs & returns count of rows dropped for missing `job_url`.
- **Per-task proxy rotation in UI** (`app.py`): `run_extraction` rotates proxy by task index and displays dropped count.
- **Data quality score preservation** (`app.py`): `_prepare_frame` uses `pd.notna()` to keep legitimate `0` scores.

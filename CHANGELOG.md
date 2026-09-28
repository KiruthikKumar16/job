# Changelog

All notable changes to the Job Market Scraper project are documented here.

## [Unreleased]

### Added
- **Live extraction progress and duration estimates** (`app.py`): show an approximate duration before searching, elapsed time and ETA as queries finish, per-query result counts, and a bounded work log for extraction stages and source errors.
- **Dashboard experience filters and large-result safeguards** (`app.py`): filter the saved dataset by minimum and maximum experience, cap rendered tables at 500 rows, and keep the filtered CSV download complete.
- **Explicit extraction-versus-display behavior** (`app.py`): experience selections narrow the scrape result table while the complete enriched run is saved to SQLite and timestamped exports.
- **Per-site rate limiting** (`scraper.py`): configurable minimum request interval via `JOB_SCRAPER_MIN_REQUEST_INTERVAL` env var (default 1.0 s). Applied to initial fetch, proxy retries, and Playwright fallback.
- **Proxy pool caching with TTL** (`proxy_manager.py`): module-level cache with thread-safe updates; TTL configurable via `PROXY_POOL_TTL` env var (default 300 s).
- **Expanded skill catalog** (`parser.py`, `skill_catalog.json`): loaded from JSON at runtime; includes analyst/BI tools (Power BI, Tableau, Qlik, Looker, etc.), additional languages, frameworks, cloud platforms, and databases.
- **CLI `--hours-old` flag** (`main.py`): filter jobs by posting freshness.
- **CLI extraction-run recording** (`main.py`): `storage.save_extraction_run()` called after every CLI run so results appear in the Streamlit "Recent extraction health" panel.
- **Environment variable support** (`main.py`, `app.py`): `JOB_DATABASE_PATH`, `JOB_MAX_WORKERS`, `PROXY_LIST`.
- **Filter: keep unclassified experience rows** (`filter.py`): rows with `NaN` experience and `seniority == "Not Specified"` are no longer dropped when experience filters are active.

### Changed
- **Dashboard cache lifetime and invalidation** (`app.py`): cache dashboard and CSV data for five minutes, use export timestamps only when SQLite is unavailable, and load CSV data only when that source is selected.
- **Dashboard memory use** (`storage.py`): omit both full and raw descriptions from the dashboard query.
- **Experience range semantics** (`app.py`, `job_filters.py`): treat the UI's 0–40 year endpoints as unbounded and retain genuinely unclassified listings during experience filtering.
- **Search worker configuration** (`app.py`): share a validated worker-count calculation between extraction and its duration estimate.
- **Thread-safe Naukri session** (`scraper.py`): replaced global `requests.Session()` with `threading.local()` for per-thread connection pooling.
- **SQLite column types** (`storage.py`): numeric columns stored as `REAL`; automatic migration from old all-TEXT schema.
- **CLI `--degree` argument** (`main.py`): now accepts space- or comma-separated values with validation.

### Fixed
- **Empty-source error reporting** (`scraper.py`): surface captured JobSpy errors when a search returns no rows, and classify HTTP 406 responses as blocked instead of successful empty searches.
- **Glassdoor location normalization** (`scraper.py`): remove only a redundant trailing country name while preserving city and region details.
- **Browser-card metadata annotation** (`scraper.py`): `card_summary` added to `NORMALIZED_COLUMNS`; `description = None` for browser cards to prevent card-text misuse.
- **Export filename constant** (`storage.py`): `EXPORT_BASE_FILENAME` used consistently; `save_to_sqlite` logs & returns count of rows dropped for missing `job_url`.
- **Per-task proxy rotation in UI** (`app.py`): `run_extraction` rotates proxy by task index and displays dropped count.
- **Data quality score preservation** (`app.py`): `_prepare_frame` uses `pd.notna()` to keep legitimate `0` scores.

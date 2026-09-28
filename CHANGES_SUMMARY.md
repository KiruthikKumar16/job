# Summary of Changes

## Fixes Implemented

### 1. Metadata Annotation in Scraper Recovery Branches
- **File**: `scraper.py`
- **Changes**:
  - Added `"card_summary"` to `NORMALIZED_COLUMNS`.
  - Updated `_normalise_jobspy` to set `card_summary = None` for JobSpy results.
  - Updated `_extract_browser_cards` to set `description = None` and `card_summary = card_text` to prevent misuse of card summary as real job description.

### 2. File Pattern Matching for Job Exports
- **File**: `storage.py`
- **Changes**:
  - Added `EXPORT_BASE_FILENAME = "job_market_export"` constant (already present from earlier work).
  - Updated `save_to_sqlite` to log warning and return count of rows dropped due to missing `job_url`.
  - Updated `save_to_files` to use `EXPORT_BASE_FILENAME` constant (already present).

### 3. Proper Proxy Rotation in Web UI
- **File**: `app.py`
- **Changes**:
  - Modified `run_extraction` to implement per-task proxy rotation using task index.
  - Modified `run_extraction` to capture and display dropped `job_url` count from `save_to_sqlite`.
  - Updated `show_scrape_section` to show dropped count in success message.

### 4. Data Quality Score Preservation Logic
- **File**: `app.py`
- **Changes**:
  - Fixed `_prepare_frame` to preserve legitimate `0` data_quality_score values using `pd.notna()` check (already present from earlier work).

### 5. Handling of Missing Job URLs and Browser Card Description Fields
- **Files**: `scraper.py`, `storage.py`, `app.py`
- **Changes**:
  - As described in items 1 and 3 above.

### 6. Per-site Rate Limiting
- **File**: `scraper.py`
- **Changes**:
  - Added `_MIN_REQUEST_INTERVAL` constant (configurable via environment variable `JOB_SCRAPER_MIN_REQUEST_INTERVAL`, default 1.0 seconds).
  - Added `_last_request_times` dictionary and `_request_lock` for thread-safe tracking of last request times per site.
  - Added `_rate_limit_site(site)` function that ensures at least `_MIN_REQUEST_INTERVAL` seconds have passed since the last request to the given site.
  - Integrated `_rate_limit_site(site)` calls in `fetch_jobs` for every platform (including Naukri, which also retains its existing random sleep).
  - Applied rate limiting to initial JobSpy/Naukri fetch, proxy retries, and Playwright fallback.

### 7. Proxy Pool Caching with TTL
- **File**: `proxy_manager.py`
- **Changes**:
  - Added module-level cache for proxy pool (`_PROXY_POOL_CACHE`, `_PROXY_POOL_TIMESTAMP`, `_PROXY_POOL_LOCK`).
  - Added configurable TTL via environment variable `PROXY_POOL_TTL` (default 300 seconds).
  - Modified `get_proxy_pool` to return a cached pool if it is still within the TTL window, otherwise fetch and validate a new pool and update the cache.
  - The cache is updated in a thread-safe manner using a lock.

### 8. Expanded Skill Catalog for Analyst/BI Roles
- **File**: `parser.py` and new `skill_catalog.json`
- **Changes**:
  - Replaced hardcoded `SKILL_CATALOG` list with a loader that reads from `skill_catalog.json` in the same directory, falling back to an expanded default list if the file is missing or invalid.
  - Expanded the catalog to include analyst/BI tools such as Excel, Power BI, Tableau, Qlik, Looker, SSRS, SAP BusinessObjects, MicroStrategy, Metabase, as well as additional programming languages, frameworks, cloud platforms, databases, data processing tools, and more.
  - The catalog is now configurable without code changes by updating the JSON file.
  - Updated `parser.py` to import `logging`, `json`, `os`, and define a `_load_skill_catalog` function that performs the loading with appropriate logging.

### 9. CLI Enhancements to Match Streamlit UI
- **File**: `main.py`
- **Changes**:
  - Added `--hours-old` argument to `build_parser()` to filter jobs by posting freshness, passed through to `fetch_jobs()`.
  - After a successful run, call `storage.save_extraction_run()` to record the run in the SQLite database, making CLI runs visible in the "Recent extraction health" dashboard panel. The call includes the same schema as used in `app.py`: run_id, started_at, finished_at, raw_count, valid_count, status, and platform_status.
  - Fixed `--degree` argument to accept space or comma-separated values (consistent with `--terms`, `--skills`, `--locations`) and added validation against the allowed list of degree labels.

### 10. Thread-Safe Naukri Session
- **File**: `scraper.py`
- **Changes**:
  - Replaced the module-level `requests.Session()` with a thread-local session (`threading.local()`) to ensure each worker thread has its own session, avoiding concurrency issues while preserving connection-pooling benefits within each thread.
  - Removed the global `_naukri_session` and added `_naukri_local = threading.local()`.
  - In `_naukri_fetch`, get or create a session for the current thread.

### 11. Environment Variable Support
- **File**: `main.py` and `app.py`
- **Changes**:
  - Added support for `JOB_DATABASE_PATH` environment variable to set the default database path for both CLI (`--db` argument) and UI (defaults to `jobs.db` if not set).
  - Added support for `JOB_MAX_WORKERS` environment variable to control the maximum number of worker threads in the UI's ThreadPoolExecutor (used in `run_extraction()`).
  - Added support for `PROXY_LIST` environment variable to provide a default proxy list for both CLI (`--user-proxies` argument) and UI (proxy textarea).
  - All environment variables are read using `os.environ.get()` with appropriate fallbacks to existing defaults.

### 12. SQLite Column Type Fix
- **File**: `storage.py`
- **Changes**:
  - Modified `save_to_sqlite` to create columns with appropriate SQLite types: `REAL` for `min_exp`, `max_exp`, `data_quality_score`, `salary_min`, `salary_max`; `TEXT` for all other columns (including datetimes stored as ISO strings).
  - Added lightweight migration logic in `_ensure_table_schema` that detects existing `jobs.db` files with the old all-TEXT schema and recreates the table with correct types, preserving data via casting.
  - Migration runs automatically on first use after upgrade; users can also delete `jobs.db` and re-run extraction to start fresh.

### 13. Filter Experience Handling for Unclassified Rows
- **File**: `filter.py`
- **Changes**:
  - Modified `filter_jobs` to keep rows with no numeric experience detected (`min_exp` and `max_exp` both NaN) AND `seniority == "Not Specified"` when `max_exp` or `min_exp` filters are applied.
  - Added explicit `| (min_values.isna() & seniorities.eq("Not Specified"))` (and equivalent for max) to both filter conditions.
  - This aligns with the app's philosophy of not dropping jobs for missing data (e.g., `date_posted` NaT is kept).
  - Added a test in `tests/test_filter.py` asserting this behavior.

## Test Updates
- Updated test files (`test_proxy_rotation.py`, `test_prepare_frame.py`, `test_load_jobs.py`, `test_pipeline.py`) to fix import paths by adding the project root to `sys.path`.
- Updated `test_proxy_rotation.py` to assert that dropped count is zero when mock data has valid job URLs.
- Added `tests/test_filter.py` to test filter behavior for unclassified experience rows.
- Verified that the expanded skill catalog works correctly by running the existing test suite; all tests pass.
- Verified CLI changes: `--hours-old` appears in help, `--degree` accepts comma-separated values, and invalid degrees trigger an error message.
- Verified CLI changes: a test run with `--max-results 0` successfully records an entry in `extraction_runs`.

## Verification
- Modified files compile without syntax errors.
- Unit tests for proxy rotation, data quality score preservation, and skill extraction pass.
- Integration tests for job loading and pipeline components pass when run directly.
- CLI changes tested for argument parsing (including `--degree` formats) and extraction run recording.
- Verified SQLite schema migration works correctly by inserting test data with numeric columns and confirming they are stored as REAL and can be sorted/filtered numerically.
- Verified filter changes via new test that unclassified rows are retained when experience filters are active.

## Notes
- The optional stretch goal for Naukri-specific description fetching was not implemented as it was marked as optional.
- All explicitly requested fixes have been implemented and tested.
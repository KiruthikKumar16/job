# Job Market Explorer

V2 is a Python job scraping, qualification extraction, filtering, persistence, and Streamlit analytics application for public job listings. V1 remains the baseline commit in Git history.

## Features

- Collect listings from LinkedIn, Indeed, Glassdoor, and Naukri through the scraper pipeline.
- Normalize job records and retain listings even when descriptions are missing.
- Extract technical degrees with exact regex matching and generic degree fallbacks.
- Detect skills independently with word-boundary matching.
- Classify experience, seniority, and work mode.
- Persist enriched data to SQLite and timestamped CSV/JSON exports.
- Explore qualifications, skills, locations, seniority, and experience in the Streamlit dashboard.
- Run independent platform/location searches concurrently with per-query progress and graceful partial failures.
- Track source metadata, data-quality scores, missing descriptions, and extraction-run history.
- Filter the dashboard by source, work mode, posted date, and minimum data quality.
- Select multiple default job roles such as Data Analyst, Data Engineer, and Full Stack Developer in both app sections.
- Use preset or custom hours/days posting windows during extraction and in Analytics, including custom calendar ranges.
- Set a numeric maximum result count with a slider or drag the right endpoint to `No limit`.
- Export files use the readable `job_market_export_<timestamp>` name.
- Choose any local CSV from the Analytics data-source selector; flexible headers are mapped and re-enriched automatically.
- Run regression tests in CI and package the app with Docker.

## Requirements

Use CPython 3.10, 3.11, or 3.12. The pinned JobSpy dependency uses NumPy 1.26.3, which may not have a wheel for newer Python versions on Windows.

## Windows setup

```powershell
py -3.12 -m venv .venv312
.venv312\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Run the web application

```powershell
.venv312\Scripts\python.exe -m streamlit run app.py
```

Open the local URL shown by Streamlit, normally `http://localhost:8501`.

Use **Scrape & Extract Data** to configure a search and save results. Use **Analytics Dashboard** to filter and visualize the latest SQLite dataset.

## Run from the command line

```powershell
.venv312\Scripts\python.exe main.py `
  --terms "Data Engineer" `
  --locations "Bengaluru, India" "Hyderabad, India" `
  --platforms linkedin,indeed,glassdoor,naukri `
  --skills Python SQL `
  --degree B.Tech B.E. M.Tech BS MS `
  --min-exp 0 `
  --max-exp 2 `
  --max-results 50
```

The CLI also accepts comma-separated values for options that support lists.

## Output

The application writes the following local files:

- `jobs.db`: SQLite database used by the dashboard.
- `jobs_<timestamp>.csv`: portable tabular export.
- `jobs_<timestamp>.json`: portable JSON export.

These generated files are intentionally excluded from Git. Run a new extraction to regenerate them.

## V2 development checks

```powershell
.venv312\Scripts\python.exe -m pytest -q
.venv312\Scripts\python.exe -m py_compile app.py filter.py main.py parser.py scraper.py storage.py
```

Build and run the container:

```powershell
docker build -t job-market-explorer .
docker run --rm -p 8501:8501 job-market-explorer
```

GitHub Actions runs the test suite and Python compilation checks for pushes and pull requests targeting `main`.

## Data access and scraper notes

Only collect pages and data you are permitted to access. Job-board markup, APIs, rate limits, and access policies change frequently. The scraper handles common blocks and continues where possible, but it cannot guarantee coverage or bypass CAPTCHAs and access challenges. Treat public proxies as untrusted and never use them with credentials or sensitive traffic.

## Project layout

- `app.py`: Streamlit interface and dashboard.
- `main.py`: command-line orchestration.
- `scraper.py`: JobSpy and browser collection strategies.
- `parser.py`: qualification, skill, experience, and work-mode extraction.
- `filter.py`: qualification, skill, seniority, and experience filtering.
- `storage.py`: SQLite, CSV, and JSON persistence.
- `proxy_manager.py`: optional public proxy validation.

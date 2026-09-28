# Job Market Explorer

Job Market Explorer is a Python and Streamlit app for collecting, deduplicating, and analyzing job listings. It also includes deterministic resume matching and optional, fact-constrained resume tailoring.

## Screenshots

<!-- Add screenshots at these paths when available. -->

**Dashboard** — screenshot placeholder (`docs/images/dashboard.png`)

**Resume tailoring** — screenshot placeholder (`docs/images/resume-tailor.png`)

## Quickstart

Requires Python 3.10, 3.11, or 3.12.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install chromium
streamlit run app.py
```

Open the local URL printed by Streamlit, usually <http://localhost:8501>. Choose **Scrape & Extract Data** to search, or **Analytics Dashboard** to browse stored jobs and import a CSV.

For the command-line interface:

```powershell
python main.py --terms "Data Engineer" --locations "Bengaluru, India" `
  --platforms linkedin,indeed,glassdoor,naukri --max-results 50
```

Run `python main.py --help` to see all search and filtering options. A Docker image can be built and started with:

```sh
docker build -t jobmarket .
docker run --rm -p 8501:8501 jobmarket
```

## Resume workflow

1. Copy `resume/master_resume.example.json` to `resume/master_resume.json` and replace its sample content with your own. The personal file is Git-ignored.
2. Configure an LLM provider and API key as described in [Configuration](#configuration). Tailoring is optional; local matching and ranking are deterministic and do not call an LLM.
3. Open **Tailor Resume**, choose a saved job, and review the match score and matched or missing skills. The page warns when the job description is unavailable or blocked.
4. Generate tailored bullets, review them alongside the originals, edit the text, and download the ATS-friendly DOCX.

The tailoring prompt excludes phone, email, and address. The output validator checks generated claims against the master resume and reports unmet required skills as gaps. Generated documents are saved under `outputs/` with the job id and timestamp; this directory is Git-ignored.

## Configuration

Put local settings in `.env` (copy `.env.example`) or set them in the process environment. Do not commit API keys or personal resume data.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JOB_DATABASE_PATH` | `jobs.db` | SQLite database path. Relative paths are resolved from the project directory. |
| `JOB_MAX_WORKERS` | Automatic | Maximum concurrent search workers. |
| `JOB_SCRAPER_MIN_REQUEST_INTERVAL` | `1.0` seconds | Minimum delay between scraper requests. |
| `PROXY_LIST` | Empty | Optional comma-separated proxy URLs for searches. Keep this empty unless you have a permitted, trusted proxy. |
| `PROXY_POOL_TTL` | `300.0` seconds | Cache lifetime for the optional public-proxy pool. |
| `JOBMARKET_LLM_PROVIDER` | `openai` | Provider name for resume tailoring. |
| `JOBMARKET_LLM_MODEL` | Required for tailoring | Model name supported by the provider endpoint. |
| `JOBMARKET_LLM_API_KEY` | Empty | API key. If empty, the app also checks `<PROVIDER>_API_KEY`, such as `OPENAI_API_KEY`. |
| `JOBMARKET_LLM_BASE_URL` | Provider default | Optional OpenAI Chat Completions-compatible endpoint URL. |
| `JOBMARKET_LLM_TIMEOUT` | `60` seconds | Timeout for a tailoring API request. |

Public proxy use is opt-in in the application and public proxies are never used with credentials. Treat public proxies as untrusted and avoid sending them sensitive traffic.

## Project layout

```text
.
├── app.py                     # Thin Streamlit entry point
├── main.py                    # CLI entry point
├── jobmarket/
│   ├── app.py                  # Streamlit pages and app logic
│   ├── cli.py                  # CLI implementation
│   ├── scraper.py              # Collection and description retrieval
│   ├── job_parser.py           # Degree, skills, sections, and requirements
│   ├── job_filters.py          # Listing filters
│   ├── storage.py              # SQLite, migrations, CSV/JSON exports
│   ├── dedup.py                # Cross-source deduplication
│   ├── csv_import.py           # Flexible CSV import
│   ├── proxy_manager.py        # Optional proxy support
│   └── resume/                 # Resume models, matching, tailoring, DOCX
├── resume/
│   └── master_resume.example.json
├── tests/                      # Pytest suite and fixtures
├── .github/workflows/ci.yml    # Python 3.10–3.12 CI
├── Dockerfile
└── pyproject.toml
```

## Troubleshooting

- **Playwright or browser launch errors:** install the browser for the active environment with `python -m playwright install chromium`. The Docker image installs Chromium and its runtime libraries during the build.
- **Tailoring says configuration is incomplete:** set `JOBMARKET_LLM_MODEL` and the API key variable for your provider. For a compatible custom endpoint, set `JOBMARKET_LLM_BASE_URL`.
- **A job has no usable description:** boards may omit descriptions or block detail requests. The listing is retained and its description status is shown in the app.
- **Few or no results / access challenge:** board availability, terms, rate limits, and page markup change. The app can report partial source failures, but does not guarantee results or bypass CAPTCHAs.
- **CSV import fails:** check that the file is a readable CSV and has recognizable job fields such as title and company. Analytics displays import errors in the page.
- **Database appears stale:** start a new extraction to refresh saved listings and dashboard data. Keep the database file writable by the account running the app.

## Terms of service and responsible use

Job boards set their own terms, access rules, and rate limits. Scraping or automated collection may violate a site's terms of service, even when pages are publicly viewable. Review and follow the applicable terms and laws before collecting data. Use this tool for personal or research purposes, collect only data you are permitted to access, and respect rate limits and access controls. Do not use it to evade CAPTCHAs or other restrictions. You are responsible for how you use the software and for complying with applicable rules.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

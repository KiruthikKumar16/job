# Contributing

Thanks for helping improve Job Market Explorer. Keep changes focused, add or update tests for behavior changes, and avoid committing `.env`, `resume/master_resume.json`, `jobs.db`, exports, or generated files under `outputs/`.

## Development setup

Use Python 3.10, 3.11, or 3.12. From the repository root:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

## Checks

Run these before opening a pull request:

```powershell
ruff check .
ruff format --check .
mypy
pytest --cov=jobmarket --cov-report=term-missing --cov-fail-under=50
python -m compileall -q app.py main.py jobmarket
```

The same checks are run by GitHub Actions for Python 3.10, 3.11, and 3.12. To run the configured hooks over the repository:

```powershell
pre-commit run --all-files
```

## Pull requests

- Explain the user-visible change and how you verified it.
- Include regression tests for bug fixes and tests for new parsing or persistence behavior.
- Keep credentials and personal data out of commits. Use the example resume and fixture data for tests.
- Follow job-board terms, access restrictions, and rate limits when working on collection behavior.

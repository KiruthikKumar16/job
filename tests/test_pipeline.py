import pandas as pd

from filter import filter_jobs
from parser import enrich_jobs, extract_qualification, extract_skills
from storage import load_extraction_runs, save_extraction_run, save_to_files
from app import normalize_imported_csv


def test_generic_qualification_is_never_unspecified():
    assert extract_qualification("Bachelor's in Computer Science", "Data Engineer") == "Bachelor's (Computer Science/IT)"
    assert extract_qualification("Degree in IT", "Data Engineer") == "Bachelor's (Information Technology)"
    assert extract_qualification("Degree required", "Data Engineer") == "Degree Required"
    assert extract_qualification("", "Data Engineer") == "Degree Required"


def test_title_fallback_extracts_skills_and_seniority():
    assert "Python" in extract_skills("", "Junior Python Engineer")
    enriched = enrich_jobs(pd.DataFrame([{"title": "Junior Python Engineer", "description": ""}]))
    assert enriched.loc[0, "seniority"] == "Entry-Level"
    assert bool(enriched.loc[0, "description_missing"])


def test_required_skills_are_independent_and_all_required():
    frame = pd.DataFrame([
        {"title": "Data Engineer", "description": "Python and SQL", "qualification": "B.Tech"},
        {"title": "Data Engineer", "description": "Python only", "qualification": "B.Tech"},
    ])
    result = filter_jobs(frame, required_skills=["Python", "SQL"])
    assert len(result) == 1


def test_run_history_and_export_keep_qualification(tmp_path):
    database = tmp_path / "jobs.db"
    save_extraction_run(str(database), {
        "run_id": "run-1", "started_at": "2026-08-31T00:00:00Z", "finished_at": "2026-08-31T00:01:00Z",
        "raw_count": 2, "valid_count": 2, "status": "completed", "platform_status": {"linkedin": "success"},
    })
    assert load_extraction_runs(str(database)).loc[0, "run_id"] == "run-1"
    _, csv_path = save_to_files(pd.DataFrame([{"title": "A", "qualification": None}]), str(tmp_path / "jobs"))
    exported = pd.read_csv(csv_path)
    assert exported.loc[0, "qualification"] == "Degree Required"


def test_arbitrary_csv_headers_are_normalized_and_enriched():
    frame = normalize_imported_csv(pd.DataFrame({
        "Job Name": ["Data Analyst"],
        "Employer": ["Acme"],
        "City": ["Pune"],
        "Apply Link": ["https://example.com/a"],
        "Job Details": ["Python SQL Bachelor's in Computer Science"],
    }))
    assert frame.loc[0, "title"] == "Data Analyst"
    assert frame.loc[0, "qualification"] == "Bachelor's (Computer Science/IT)"
    assert frame.loc[0, "extracted_skills"] == ["Python", "SQL"]

import json
from pathlib import Path

import pytest
from jobmarket.resume import (
    ResumeLoadError,
    load_master_resume,
    validate_master_resume,
)

EXAMPLE = Path(__file__).parents[1] / "resume" / "master_resume.example.json"


def test_example_resume_validates_and_every_bullet_has_an_id():
    resume = load_master_resume(EXAMPLE)
    assert resume.contact.name == "Alex Example"
    assert resume.skills["Programming"] == ["Python", "SQL", "TypeScript"]

    bullets = [bullet for experience in resume.experience for bullet in experience.bullets] + [
        bullet for project in resume.projects for bullet in project.bullets
    ]
    assert bullets
    assert all(bullet.id and bullet.text for bullet in bullets)
    assert bullets[0].tags == ["Python", "Data Engineering"]
    assert bullets[0].metrics["latency_reduction"] == "35%"


def test_plain_text_bullets_receive_unique_generated_ids():
    resume = validate_master_resume(
        {
            "contact": {"name": "Sample Person"},
            "experience": [
                {
                    "role": "Engineer",
                    "company": "Example Co",
                    "dates": {"start": "2024-01", "end": "Present"},
                    "bullets": ["Shipped feature A", "Reduced processing time"],
                }
            ],
        }
    )
    first, second = resume.experience[0].bullets
    assert first.text == "Shipped feature A"
    assert first.id != second.id
    assert first.tags is None
    assert first.metrics is None


def test_validation_error_names_the_invalid_field():
    with pytest.raises(ResumeLoadError, match=r"experience\.0\.role: Field required"):
        validate_master_resume(
            {
                "contact": {"name": "Sample Person"},
                "experience": [{"company": "Example Co", "dates": {"start": "2024"}}],
            }
        )


def test_missing_resume_file_has_copy_example_guidance(tmp_path):
    with pytest.raises(ResumeLoadError, match="Copy resume/master_resume.example.json"):
        load_master_resume(tmp_path / "missing.json")


def test_invalid_json_reports_line_and_column(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"contact": ', encoding="utf-8")
    with pytest.raises(ResumeLoadError, match=r"line 1, column"):
        load_master_resume(path)


def test_non_utf8_file_has_clear_error(tmp_path):
    path = tmp_path / "not_utf8.json"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ResumeLoadError, match="must be valid UTF-8"):
        load_master_resume(path)


def test_invalid_file_reports_validation_errors(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps({"contact": {"name": "Sample"}, "unexpected": True}), encoding="utf-8"
    )
    with pytest.raises(
        ResumeLoadError, match="failed validation: unexpected: Extra inputs are not permitted"
    ):
        load_master_resume(path)

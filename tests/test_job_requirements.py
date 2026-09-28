import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from jobmarket.job_parser import enrich_jobs, extract_requirements, split_sections
from jobmarket.storage import save_to_sqlite

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = [
    "requirements_data_engineer.txt",
    "requirements_qa_analyst.txt",
    "requirements_product_analyst.txt",
    "requirements_customer_success.txt",
    "requirements_software_developer.txt",
]


@pytest.mark.parametrize("filename", SAMPLES)
def test_realistic_descriptions_split_into_expected_sections(filename):
    sections = split_sections((FIXTURES / filename).read_text(encoding="utf-8"))
    assert set(sections) == {"responsibilities", "requirements", "nice_to_have", "other"}
    assert sections["responsibilities"]
    assert sections["requirements"]
    assert sections["nice_to_have"]
    title = filename.removeprefix("requirements_").removesuffix(".txt").replace("_", " ").title()
    assert title.casefold() in sections["other"].casefold()


@pytest.mark.parametrize(
    ("filename", "required", "preferred"),
    [
        (SAMPLES[0], {"Python", "SQL"}, {"Snowflake", "Spark", "AWS"}),
        (SAMPLES[1], {"Java", "Git"}, {"Python", "Docker"}),
        (SAMPLES[2], {"SQL", "Tableau", "Excel"}, {"Python", "Looker"}),
        (SAMPLES[3], {"REST API", "Linux", "SQL"}, {"AWS", "Kubernetes"}),
        (SAMPLES[4], {"Java", "SQL", "Git"}, {"Docker", "Kubernetes", "AWS", "Kafka"}),
    ],
)
def test_skills_follow_the_section_they_appear_in(filename, required, preferred):
    description = (FIXTURES / filename).read_text(encoding="utf-8")
    requirements = extract_requirements(description)
    assert required <= set(requirements["required_skills"])
    assert preferred <= set(requirements["preferred_skills"])


@pytest.mark.parametrize(
    ("filename", "experience", "education"),
    [
        (SAMPLES[0], "4+ years", "Bachelor's"),
        (SAMPLES[1], "3-5 years", "Bachelor's"),
        (SAMPLES[2], "2-4 years", "Master's"),
        (SAMPLES[3], "2+ years", "Bachelor's"),
        (SAMPLES[4], "5 years", "B.Tech"),
    ],
)
def test_extracts_experience_and_education(filename, experience, education):
    requirements = extract_requirements((FIXTURES / filename).read_text(encoding="utf-8"))
    assert experience in requirements["years_experience"]
    assert any(education in value for value in requirements["education"])
    assert requirements["keywords"]


def test_colon_and_bullet_headers_switch_section_and_keep_inline_text():
    sections = split_sections("""Other intro
- Must have: Python and SQL
• Preferred: Docker
RESPONSIBILITIES:
* Build APIs
""")
    assert "Other intro" in sections["other"]
    assert "Python and SQL" in sections["requirements"]
    assert "Docker" in sections["nice_to_have"]
    assert "Build APIs" in sections["responsibilities"]


def test_unclassified_title_is_not_a_required_skill_when_sections_are_present():
    result = extract_requirements("Python Developer\n\nRequirements:\n- SQL experience")
    assert "Python" not in result["required_skills"]
    assert "SQL" in result["required_skills"]


def test_unheaded_description_uses_other_as_default_requirement_text():
    result = extract_requirements("Seeking Python and SQL experience with AWS.")
    assert {"Python", "SQL", "AWS"} <= set(result["required_skills"])


def test_extracts_multiple_explicit_degree_options():
    result = extract_requirements("Requirements:\nB.Tech or B.E. in Computer Science.")
    assert {"B.Tech", "B.E."} <= set(result["education"])


def test_enriched_requirements_are_json_and_are_persisted(tmp_path):
    description = (FIXTURES / SAMPLES[0]).read_text(encoding="utf-8")
    enriched = enrich_jobs(
        pd.DataFrame(
            [
                {
                    "site": "linkedin",
                    "title": "Data Engineer",
                    "job_url": "https://jobs.example/req",
                    "description": description,
                }
            ]
        )
    )
    parsed = json.loads(enriched.loc[0, "requirements_json"])
    assert "Python" in parsed["required_skills"]
    assert "Snowflake" in parsed["preferred_skills"]
    assert "4+ years" in parsed["years_experience"]

    database = tmp_path / "requirements.db"
    save_to_sqlite(enriched, str(database))
    with sqlite3.connect(database) as connection:
        stored = json.loads(
            connection.execute(
                "SELECT requirements_json FROM jobs WHERE job_url = ?",
                ("https://jobs.example/req",),
            ).fetchone()[0]
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
    assert stored == parsed
    assert "requirements_json" in columns

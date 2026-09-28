import json

from jobmarket.resume import MasterResume, match_resume


def sample_resume() -> MasterResume:
    return MasterResume.model_validate(
        {
            "contact": {"name": "Example Candidate"},
            "skills": {
                "Languages": ["JavaScript", "Python"],
                "Databases": ["Postgres"],
            },
            "experience": [
                {
                    "role": "Data Engineer",
                    "company": "Example Analytics",
                    "dates": {"start": "2022-01", "end": "Present"},
                    "bullets": [
                        {
                            "id": "strong-evidence",
                            "text": "Built Python and SQL pipelines on AWS for real-time analytics.",
                            "tags": ["AWS", "Data Engineering"],
                        },
                        {
                            "id": "partial-evidence",
                            "text": "Automated data checks and reporting with Python, JavaScript, and Postgres.",
                        },
                    ],
                }
            ],
            "projects": [
                {
                    "name": "Warehouse Modernization",
                    "tech": ["Python", "SQL"],
                    "bullets": [
                        {
                            "id": "project-evidence",
                            "text": "Migrated reporting workloads to a scalable data warehouse.",
                        }
                    ],
                }
            ],
        }
    )


def test_synonyms_match_skills_and_are_reported_using_job_terms():
    result = match_resume(
        sample_resume(),
        {
            "required_skills": ["JS", "PostgreSQL"],
            "preferred_skills": ["Docker"],
        },
    )
    assert result["matched_skills"] == ["JS", "PostgreSQL"]
    assert result["missing_required_skills"] == []
    assert result["missing_preferred_skills"] == ["Docker"]
    assert 0 <= result["match_score"] <= 100


def test_synonyms_are_used_when_ranking_resume_evidence():
    result = match_resume(
        sample_resume(),
        json.dumps(
            {
                "keywords": ["js", "postgres"],
            }
        ),
    )
    assert result["top_items"]
    assert "javascript" in result["top_items"][0]["matched_terms"]
    assert "postgresql" in result["top_items"][0]["matched_terms"]


def test_empty_requirements_return_zero_and_no_recommendations():
    result = match_resume(sample_resume(), {})
    assert result == {
        "match_score": 0,
        "matched_skills": [],
        "missing_required_skills": [],
        "missing_preferred_skills": [],
        "top_items": [],
    }
    assert match_resume(sample_resume(), None)["match_score"] == 0


def test_evidence_is_ranked_by_keyword_overlap_with_stable_order():
    requirements = {"keywords": ["Python", "SQL", "AWS"]}
    first = match_resume(sample_resume(), requirements, top_n=10)
    second = match_resume(sample_resume(), requirements, top_n=10)

    assert first == second
    ranked = first["top_items"]
    assert ranked[0]["id"] == "strong-evidence"
    assert ranked[0]["relevance_score"] == 100
    assert [item["relevance_score"] for item in ranked] == sorted(
        (item["relevance_score"] for item in ranked),
        reverse=True,
    )
    assert any(item["item_type"] == "project" for item in ranked)
    assert len(match_resume(sample_resume(), requirements, top_n=2)["top_items"]) == 2


def test_missing_required_skills_reduce_score_and_missing_preferred_are_reported():
    result = match_resume(
        sample_resume(),
        {
            "required_skills": ["Python", "Kubernetes"],
            "preferred_skills": ["Docker", "PostgreSQL"],
        },
    )
    assert result["matched_skills"] == ["Python", "PostgreSQL"]
    assert result["missing_required_skills"] == ["Kubernetes"]
    assert result["missing_preferred_skills"] == ["Docker"]
    assert result["match_score"] < 100


def test_skill_boundaries_do_not_confuse_c_with_cpp_or_sql_with_nosql():
    resume = MasterResume.model_validate(
        {
            "contact": {"name": "Example Candidate"},
            "skills": {"Languages and databases": ["C++", "NoSQL"]},
        }
    )
    result = match_resume(resume, {"required_skills": ["C", "SQL"]})
    assert result["matched_skills"] == []
    assert result["missing_required_skills"] == ["C", "SQL"]

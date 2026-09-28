import pytest
from jobmarket.job_parser import (
    extract_experience_and_seniority,
    extract_qualification,
    extract_skills,
    extract_work_mode,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BS in CS", "BS (Computer Science/IT)"),
        ("B.S. in computer science", "BS (Computer Science/IT)"),
        ("B.S., in computer science", "BS (Computer Science/IT)"),
        ("B.S in CS", "BS (Computer Science/IT)"),
        ("b.s. IN cs", "BS (Computer Science/IT)"),
        ("B.E. in mechanical engineering", "B.E. (Engineering)"),
        ("BE in engineering", "B.E. (Engineering)"),
        ("B.E in engineering", "B.E. (Engineering)"),
        ("M.S. in data science", "MS (Data Science/AI)"),
        ("MS in CS", "MS (Computer Science/IT)"),
        ("M.S.; in data science", "MS (Data Science/AI)"),
        ("M.Tech in computer science", "M.Tech (Computer Science/IT)"),
        ("M.Tech. in engineering", "M.Tech (Engineering)"),
        ("m.tech in computer science", "M.Tech (Computer Science/IT)"),
        ("B.Tech in computer science", "B.Tech (Computer Science/IT)"),
        ("bachelor's degree in computer science", "Bachelor's (Computer Science/IT)"),
        ("Bachelor’s degree in computer science", "Bachelor's (Computer Science/IT)"),
        ("MASTER'S degree in data science", "Master's (Data Science/AI)"),
        ("MS Excel skills required", "Degree Required"),
        ("MS Office proficiency required", "Degree Required"),
        ("systems analyst with office skills", "Degree Required"),
        ("the word basement is unrelated", "Degree Required"),
        ("business analyst with basic communication", "Degree Required"),
        ("experience with HTML and CSS", "Degree Required"),
        ("degree in computer science", "Bachelor's (Computer Science/IT)"),
        ("Bachelors in IT", "Bachelor's (Information Technology)"),
        ("Master of Technology", "M.Tech"),
        ("B-E in engineering", "B.E. (Engineering)"),
        ("M–Tech in engineering", "M.Tech (Engineering)"),
    ],
)
def test_degree_matching_boundaries(text, expected):
    assert extract_qualification(text, "") == expected


@pytest.mark.parametrize(
    ("text", "expected_skills"),
    [
        ("C programming", {"C"}),
        ("C++ development", {"C++"}),
        ("C# development", {"C#"}),
        ("C, C++ and C#", {"C", "C++", "C#"}),
        ("R language", {"R"}),
        ("r language", {"R"}),
        ("Go developer", {"Go"}),
        ("go developer", {"Go"}),
        ("Golang developer", set()),
        ("NoSQL database", set()),
        ("SQL database", {"SQL"}),
        ("sql database", {"SQL"}),
        ("PostgreSQL database", {"PostgreSQL"}),
        ("Excel is used daily", {"Excel"}),
        ("MS Excel is used daily", {"Excel"}),
        ("JavaScript engineer", {"JavaScript"}),
        ("Java engineer", {"Java"}),
        ("Pythonista community", set()),
        ("Café data engineer", set()),
    ],
)
def test_skill_detection_respects_token_boundaries(text, expected_skills):
    skills = extract_skills(text, "")
    assert set(skills) == expected_skills


@pytest.mark.parametrize(
    ("text", "minimum", "maximum", "seniority"),
    [
        ("Requires 2-4 years of experience", 2, 4, "Entry-Level"),
        ("Requires 2–4 years of experience", 2, 4, "Entry-Level"),
        ("Requires 2—4 years experience", 2, 4, "Entry-Level"),
        ("Requires 2 to 4 yrs experience", 2, 4, "Entry-Level"),
        ("Requires 2+ years experience", 2, None, "Entry-Level"),
        ("0-1 yr experience", 0, 1, "Entry-Level"),
        ("Fresher welcome", 0, 0, "Entry-Level"),
        ("Freshers are welcome", 0, 0, "Entry-Level"),
        ("At least 3 years experience", 3, None, "Mid-Level"),
        ("5 yrs experience", 5, None, "Mid-Level"),
        ("6+ years experience", 6, None, "Senior/Lead"),
        ("Version 2.4 and 2024 release", None, None, "Not Specified"),
    ],
)
def test_experience_range_parsing(text, minimum, maximum, seniority):
    result = extract_experience_and_seniority(text, "")
    assert result["min_exp"] == minimum
    assert result["max_exp"] == maximum
    assert result["seniority"] == seniority


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hybrid work arrangement", "Hybrid"),
        ("hybrid role", "Hybrid"),
        ("Remote role", "Remote"),
        ("work from home", "Remote"),
        ("work-from-home", "Remote"),
        ("WFH available", "Remote"),
        ("telecommuting position", "Remote"),
        ("on-site role", "On-site"),
        ("onsite role", "On-site"),
        ("on site role", "On-site"),
        ("in-office role", "On-site"),
        ("office-based role", "On-site"),
        ("office administrator", "Not Specified"),
        ("remotely managed system", "Not Specified"),
        ("hybridization research", "Not Specified"),
    ],
)
def test_work_mode_matching(text, expected):
    assert extract_work_mode(text) == expected

"""Title-aware NLP normalization for Indian job listings."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import pandas as pd

LOGGER = logging.getLogger(__name__)


def _load_skill_catalog() -> list[str]:
    """Load skill catalog from JSON file, fallback to default list."""
    default_skills = [
        "Python",
        "Java",
        "JavaScript",
        "TypeScript",
        "C",
        "C++",
        "C#",
        "Scala",
        "R",
        "PHP",
        "Ruby",
        "Go",
        "Rust",
        "Kotlin",
        "Swift",
        "React",
        "Node.js",
        "Angular",
        "Vue.js",
        "Svelte",
        "Django",
        "Flask",
        "FastAPI",
        "Spring",
        "Spring Boot",
        "ASP.NET",
        "Laravel",
        "AWS",
        "Azure",
        "GCP",
        "IBM Cloud",
        "Oracle Cloud",
        "Docker",
        "Kubernetes",
        "OpenShift",
        "Mesos",
        "Jenkins",
        "GitLab CI",
        "GitHub Actions",
        "CircleCI",
        "Travis CI",
        "Terraform",
        "Ansible",
        "Chef",
        "Puppet",
        "SaltStack",
        "SQL",
        "PostgreSQL",
        "MySQL",
        "MariaDB",
        "MongoDB",
        "Redis",
        "Cassandra",
        "Oracle",
        "SQL Server",
        "SQLite",
        "DynamoDB",
        "Redshift",
        "BigQuery",
        "Snowflake",
        "PostgreSQL",
        "Azure Synapse",
        "Excel",
        "Power BI",
        "Tableau",
        "Qlik",
        "Looker",
        "SSRS",
        "SAP BusinessObjects",
        "MicroStrategy",
        "Metabase",
        "Spark",
        "Hadoop",
        "Flink",
        "Kafka",
        "RabbitMQ",
        "ActiveMQ",
        "Git",
        "SVN",
        "Mercurial",
        "Linux",
        "Unix",
        "Windows",
        "macOS",
        "REST API",
        "GraphQL",
        "gRPC",
        "SOAP",
        "HTML",
        "CSS",
        "Sass",
        "Less",
        "Bootstrap",
        "Tailwind",
        "Jest",
        "Mocha",
        "JUnit",
        "TestNG",
        "PyTest",
        "Agile",
        "Scrum",
        "Kanban",
        "Machine Learning",
        "Deep Learning",
        "TensorFlow",
        "PyTorch",
        "Scikit-learn",
        "Keras",
        "XGBoost",
        "Natural Language Processing",
        "Computer Vision",
        "Data Analysis",
        "Statistical Analysis",
        "Data Mining",
        "ETL",
        "Data Warehousing",
        "BI Reporting",
        "Shell Scripting",
        "Bash",
        "PowerShell",
    ]
    try:
        # Look for skill_catalog.json in the same directory as this file
        module_dir = os.path.dirname(os.path.abspath(__file__))
        catalog_path = os.path.join(module_dir, "skill_catalog.json")
        if os.path.exists(catalog_path):
            with open(catalog_path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and all(isinstance(item, str) for item in data):
                    return data
                else:
                    LOGGER.warning(
                        "skill_catalog.json does not contain a list of strings; using default"
                    )
        else:
            LOGGER.warning("skill_catalog.json not found; using default skill catalog")
    except Exception as e:
        LOGGER.warning("Failed to load skill_catalog.json: %s; using default", e)
    return default_skills


# Load the skill catalog once at module import
SKILL_CATALOG = _load_skill_catalog()

TITLE_ONLY_MARKER = "Extracted from Title Only"
ENTRY_PATTERN = re.compile(
    r"\b(freshers?|trainee|intern|associate|graduate|sde[- ]?1|junior|0[- ]?[12])\b", re.I
)
MID_PATTERN = re.compile(r"\b(sde[- ]?2|l4|engineer\s+ii)\b", re.I)
SENIOR_PATTERN = re.compile(
    r"\b(sr\.?|senior|lead|principal|staff|manager|vp|avp|architect)\b", re.I
)
DEGREE_PATTERNS = {
    "B.Tech": r"\b(b\.?[- ]?tech|bachelor\s+of\s+technology)\b",
    "B.E.": r"\b(b\.?[- ]?e\.?|bachelor\s+of\s+engineering)\b",
    "M.Tech": r"\b(m\.?[- ]?tech|master\s+of\s+technology)\b",
    "M.E.": r"\b(m\.?[- ]?e\.?|master\s+of\s+engineering)\b",
    "B.Sc": r"\b(b\.?sc)\b",
    "M.Sc": r"\b(m\.?sc)\b",
    "BCA": r"\b(bca)\b",
    "MCA": r"\b(mca)\b",
    # These two-letter forms overlap product names (MS Office/Excel), so
    # require wording that makes their academic meaning clear.
    "BS": r"\bb\.?s\.?(?=[,;:]?\s+(?:in|of|degree|graduate|graduated|holder|program|programme)\b)",
    "MS": r"\bm\.?s\.?(?=[,;:]?\s+(?:in|of|degree|graduate|graduated|holder|program|programme)\b)",
}
GENERIC_PATTERN = re.compile(
    r"\b(bachelor[’']?s?|master[’']?s?|degree|diploma)\b(?:\s+(?:in|of)\s+([\w\s-]+))?", re.I
)
FIELD_STOP_WORDS = re.compile(
    r"\b(?:required|preferred|optional|or|and|with|plus|years?|experience|degree|diploma)\b", re.I
)

SECTION_ALIASES = {
    "responsibilities": {
        "responsibilities",
        "keyresponsibilities",
        "jobresponsibilities",
        "duties",
        "whatyoulldo",
        "whatyoullbedoing",
        "whatyouwilldo",
        "whatyouwillbedoing",
        "abouttherole",
        "therole",
    },
    "requirements": {
        "requirements",
        "requirement",
        "qualifications",
        "qualification",
        "musthave",
        "required",
        "requiredskills",
        "requiredqualifications",
        "basicqualifications",
        "minimumqualifications",
        "skillsandexperience",
        "whatwere lookingfor",
        "whatwearelookingfor",
        "whatyoumusthave",
        "whoyouare",
        "whowearelookingfor",
        "candidateprofile",
    },
    "nice_to_have": {
        "nicetohave",
        "preferred",
        "preferredqualifications",
        "preferredskills",
        "desiredqualifications",
        "bonus",
        "goodtohave",
        "pluses",
        "wouldbeaplus",
        "itwouldbeaplus",
    },
}
TOOL_CATALOG = {
    "AWS",
    "Azure",
    "GCP",
    "IBM Cloud",
    "Oracle Cloud",
    "Docker",
    "Kubernetes",
    "OpenShift",
    "Jenkins",
    "GitLab CI",
    "GitHub Actions",
    "Terraform",
    "Ansible",
    "SQL",
    "PostgreSQL",
    "MySQL",
    "MongoDB",
    "Redis",
    "Oracle",
    "SQL Server",
    "SQLite",
    "DynamoDB",
    "Redshift",
    "BigQuery",
    "Snowflake",
    "Azure Synapse",
    "Excel",
    "Power BI",
    "Tableau",
    "Qlik",
    "Looker",
    "SSRS",
    "SAP BusinessObjects",
    "MicroStrategy",
    "Metabase",
    "Spark",
    "Hadoop",
    "Flink",
    "Kafka",
    "Git",
    "Linux",
    "Unix",
    "Windows",
    "macOS",
    "REST API",
    "GraphQL",
    "gRPC",
    "Postman",
}
REQUIREMENT_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "you",
    "your",
    "our",
    "are",
    "will",
    "have",
    "has",
    "that",
    "this",
    "from",
    "who",
    "what",
    "their",
    "they",
    "them",
    "into",
    "about",
    "such",
    "other",
    "work",
    "working",
    "team",
    "teams",
    "role",
    "years",
    "year",
    "experience",
    "ability",
    "strong",
    "excellent",
    "good",
    "plus",
    "preferred",
    "required",
    "must",
    "should",
    "can",
    "all",
    "any",
    "across",
    "using",
    "use",
    "build",
    "building",
    "develop",
    "developing",
    "support",
    "including",
}


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _combined_text(text: str, title: str) -> str:
    return f"{_safe_text(title)} {_safe_text(text)}".strip()


def _field_of_study(content: str) -> str | None:
    """Normalize common technical fields even when generic wording is used."""
    lower = content.casefold()
    if re.search(r"\b(computer science|computer engineering|computing|cs)\b", lower):
        return "Computer Science/IT"
    if re.search(r"\b(information technology|\bit\b)\b", lower):
        return "Information Technology"
    if re.search(r"\b(data science|artificial intelligence|machine learning)\b", lower):
        return "Data Science/AI"
    if re.search(r"\b(engineering)\b", lower):
        return "Engineering"
    return None


def extract_qualification(text: str, title: str) -> str:
    """Return a specific or generic degree label; every record gets a value."""
    content = _combined_text(text, title)
    content = re.sub(r"[‐‑‒–—−]", "-", content).replace("’", "'")
    field = _field_of_study(content)
    for label, pattern in DEGREE_PATTERNS.items():
        if re.search(pattern, content, re.I):
            return f"{label} ({field})" if field else label
    generic = GENERIC_PATTERN.search(content)
    if not generic:
        return "Degree Required"
    kind = generic.group(1).casefold()
    field_text = FIELD_STOP_WORDS.split((generic.group(2) or "").strip(), maxsplit=1)[0].strip(
        " ,.;:()-"
    )
    generic_field = _field_of_study(field_text) or (field_text.title() if field_text else None)
    if kind.startswith("bachelor"):
        return f"Bachelor's ({generic_field or field or 'Any Field'})"
    if kind.startswith("master"):
        return f"Master's ({generic_field or field or 'Any Field'})"
    if kind == "diploma":
        return f"Diploma ({generic_field or field or 'Any Field'})"
    return (
        f"Bachelor's ({generic_field or field})" if (generic_field or field) else "Degree Required"
    )


def extract_skills(text: str, title: str) -> list[str]:
    description = _safe_text(text)
    content = _combined_text(description, title)

    def matches_skill(skill: str) -> bool:
        escaped = re.escape(skill)
        # C-family names end in punctuation; ordinary word boundaries miss
        # C++/C# and can let C match their prefixes.
        if skill in {"C", "C++", "C#"}:
            pattern = r"(?<!\w)" + escaped + r"(?![\w+#])"
        else:
            pattern = r"(?<!\w)" + escaped + r"(?!\w)"
        return re.search(pattern, content, re.I) is not None

    skills = [skill for skill in SKILL_CATALOG if matches_skill(skill)]
    return skills if skills or description.strip() else [TITLE_ONLY_MARKER]


def _section_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold().replace("’", "'"))


def _section_for_header(value: str) -> str | None:
    key = _section_key(value.strip().strip("*_:#- "))
    for section, aliases in SECTION_ALIASES.items():
        if key in {_section_key(alias) for alias in aliases}:
            return section
    return None


def split_sections(text: str) -> dict[str, str]:
    """Split a job description into responsibilities, requirements, nice-to-have, and other.

    Recognizes standalone headings (including markdown/bullet headings) and inline
    ``Heading: content`` forms without mistaking ordinary colon punctuation for a heading.
    """
    sections: dict[str, list[str]] = {
        "responsibilities": [],
        "requirements": [],
        "nice_to_have": [],
        "other": [],
    }
    active = "other"
    for raw_line in _safe_text(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        heading_line = re.sub(r"^(?:[-*•‣▪]+\s*|#{1,6}\s*)", "", line).strip()
        header = _section_for_header(heading_line)
        remainder = ""
        if header is None and ":" in heading_line:
            possible_header, remainder = heading_line.split(":", 1)
            header = _section_for_header(possible_header)
        elif header is None:
            # A trailing colon is common on headings without a body.
            header = _section_for_header(heading_line.rstrip(":"))
        if header:
            active = header
            if remainder.strip():
                sections[active].append(remainder.strip())
            continue
        sections[active].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _skills_in_text(text: str) -> list[str]:
    """Find catalog skills without adding title-only matches."""
    if not text:
        return []
    found: list[str] = []
    for skill in SKILL_CATALOG:
        escaped = re.escape(skill)
        if skill in {"C", "C++", "C#"}:
            pattern = r"(?<!\w)" + escaped + r"(?![\w+#])"
        else:
            pattern = r"(?<!\w)" + escaped + r"(?!\w)"
        if re.search(pattern, text, re.I):
            found.append(skill)
    return found


def extract_requirements(description: str) -> dict[str, list[str]]:
    """Extract categorized skills, tools, experience, education, and keywords."""
    sections = split_sections(description)
    all_text = "\n".join(sections.values())
    required_text = "\n".join(sections[name] for name in ("responsibilities", "requirements"))
    if not required_text.strip() and not sections["nice_to_have"].strip():
        # Plain prose without recognized headings has no section signal, so treat
        # its unclassified body as the default requirements text.
        required_text = sections["other"]
    required_skills = _skills_in_text(required_text)
    preferred_skills = _skills_in_text(sections["nice_to_have"])
    tools = [skill for skill in _skills_in_text(all_text) if skill in TOOL_CATALOG]

    years: list[str] = []
    experience_pattern = re.compile(
        r"\b\d{1,2}(?:\.\d+)?\s*(?:\+\s*|(?:[-–—]|to)\s*\d{1,2}(?:\.\d+)?\s*)?(?:years?|yrs?)\b",
        re.I,
    )
    for match in experience_pattern.finditer(all_text):
        value = re.sub(r"\s+", " ", match.group(0)).replace("–", "-").replace("—", "-")
        if value.casefold() not in {item.casefold() for item in years}:
            years.append(value)

    explicit_degrees = [
        label for label, pattern in DEGREE_PATTERNS.items() if re.search(pattern, description, re.I)
    ]
    if explicit_degrees:
        education = explicit_degrees
    else:
        qualification = extract_qualification(description, "")
        education = [] if qualification == "Degree Required" else [qualification]
    keywords: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z+#.]{2,}", all_text):
        normalized = token.casefold().strip(".#")
        if normalized and normalized not in REQUIREMENT_STOP_WORDS and normalized not in keywords:
            keywords.append(normalized)
            if len(keywords) >= 40:
                break
    return {
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "tools": tools,
        "years_experience": years,
        "education": education,
        "keywords": keywords,
    }


def extract_experience_and_seniority(text: str, title: str) -> dict[str, float | str | None]:
    content = re.sub(r"[‐‑‒–—−]", "-", _safe_text(text))
    number = r"(\d{1,2}(?:\.\d+)?)"
    range_match = re.search(rf"\b{number}\s*(?:to|-)\s*{number}\s*(?:years?|yrs?)\b", content, re.I)
    single_match = re.search(rf"\b{number}\s*\+?\s*(?:years?|yrs?)\b", content, re.I)
    min_exp: float | None = None
    max_exp: float | None = None
    if range_match:
        min_exp, max_exp = float(range_match.group(1)), float(range_match.group(2))
    elif single_match:
        min_exp = float(single_match.group(1))
    elif re.search(r"\bfreshers?\b", content, re.I):
        min_exp = max_exp = 0.0
    safe_title = _safe_text(title)
    if SENIOR_PATTERN.search(safe_title) or (min_exp is not None and min_exp > 5):
        seniority = "Senior/Lead"
    elif MID_PATTERN.search(safe_title) or (min_exp is not None and 2 < min_exp <= 5):
        seniority = "Mid-Level"
    elif (
        ENTRY_PATTERN.search(safe_title)
        or re.search(r"\bfreshers?\b", content, re.I)
        or (min_exp is not None and min_exp <= 2)
    ):
        seniority = "Entry-Level"
    else:
        seniority = "Not Specified"
    return {"min_exp": min_exp, "max_exp": max_exp, "seniority": seniority}


def extract_work_mode(text: str) -> str:
    content = _safe_text(text)
    if re.search(r"\bhybrid\b", content, re.I):
        return "Hybrid"
    if re.search(r"\b(remote|work[ -]+from[ -]+home|wfh|telecommut\w*)\b", content, re.I):
        return "Remote"
    if re.search(r"\b(on[ -]?site|in[ -]?office|office[- ]based)\b", content, re.I):
        return "On-site"
    return "Not Specified"


def enrich_jobs(df: pd.DataFrame) -> pd.DataFrame:
    """Return all input jobs enriched; no job is removed for missing NLP fields."""
    result = df.copy()
    descriptions = (
        result.get("description", pd.Series("", index=result.index)).fillna("").astype(str)
    )
    raw_descriptions = (
        result.get("description_raw", pd.Series("", index=result.index)).fillna("").astype(str)
    )
    titles = result.get("title", pd.Series("", index=result.index)).fillna("").astype(str)
    result["extracted_skills"] = [
        extract_skills(text, title) for text, title in zip(descriptions, titles, strict=True)
    ]
    result["qualification"] = [
        extract_qualification(text, title) for text, title in zip(descriptions, titles, strict=True)
    ]
    experience = [
        extract_experience_and_seniority(text, title)
        for text, title in zip(descriptions, titles, strict=True)
    ]
    result["min_exp"] = [item["min_exp"] for item in experience]
    result["max_exp"] = [item["max_exp"] for item in experience]
    result["seniority"] = [item["seniority"] for item in experience]
    result["work_mode"] = descriptions.map(extract_work_mode)
    result["description_missing"] = descriptions.str.strip().eq("")
    requirement_descriptions = [
        raw.strip() or description
        for raw, description in zip(raw_descriptions, descriptions, strict=True)
    ]
    result["requirements_json"] = [
        json.dumps(extract_requirements(description), ensure_ascii=False, sort_keys=True)
        for description in requirement_descriptions
    ]
    result["data_quality_score"] = result.apply(_data_quality_score, axis=1)
    return result


def _data_quality_score(row: pd.Series) -> int:
    """Score record completeness based on enriched data fields.

    For raw scraped data, checks core fields: title, company, location, job_url, description, date_posted.
    For enriched data, also considers qualification and extracted_skills when available.
    Returns percentage of present fields (0-100).
    """
    # Core fields that should always be present after normalization
    core_fields = ("title", "company", "location", "job_url", "description", "date_posted")
    # Enriched fields that may be present after NLP processing
    enriched_fields = ("qualification", "extracted_skills")

    # Check which fields are actually present in the row
    present_core = sum(bool(row.get(field)) for field in core_fields)
    present_enriched = sum(bool(row.get(field)) for field in enriched_fields)

    # If we have enriched fields, use them; otherwise fall back to core only
    if present_enriched > 0:
        total_fields = len(core_fields) + len(enriched_fields)
        present = present_core + present_enriched
    else:
        total_fields = len(core_fields)
        present = present_core

    return round((present / total_fields) * 100) if total_fields > 0 else 0

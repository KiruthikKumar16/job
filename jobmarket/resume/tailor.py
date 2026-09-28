"""Fact-constrained LLM rewriting for selected resume bullets."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

import requests
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobmarket.job_parser import SKILL_CATALOG, TITLE_ONLY_MARKER, TOOL_CATALOG, extract_skills

from .matcher import _normalize_phrase
from .models import MasterResume


class TailoringError(ValueError):
    """Configuration, response, or factual-validation error during tailoring."""


class TailoredBullet(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class TailoredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    selected_bullets: list[TailoredBullet] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class CompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's raw JSON response text."""


SYSTEM_PROMPT = """You rewrite resume bullets for a specific job using only the supplied master-resume facts.
Never add or imply skills, tools, employers, responsibilities, outcomes, or metrics that are not present in the master resume.
You may reorder or rephrase existing facts to mirror the job description's language.
Do not fill skill gaps by claiming the candidate has those skills.
Return unmet required skills exactly in the gaps field.
Do not include contact details.
Return one strict JSON object, with no markdown or surrounding prose, using exactly this shape:
{"selected_bullets":[{"source_id":"existing source id","text":"rewritten bullet"}],"gaps":["unmet required skill"]}
Rewrite every supplied selected bullet exactly once and keep its source_id unchanged.
Do not return any other fields.

EXAMPLES OF WHAT NOT TO DO:
- Do not claim "5 years of experience with X" if the master resume only shows "3 years of experience with X"
- Do not claim expertise in "AWS Kubernetes" if the master resume only shows "basic Kubernetes usage"
- Do not claim to have led a team of 10 people if the master resume only shows individual contributor work
- Do not imply you worked at Google if the master resume shows work at a different company
- Do not claim to have improved performance by 50% if the master resume doesn't contain that metric

EXAMPLES OF WHAT TO DO:
- Rephrase "Developed Python applications for data processing" to "Built data processing pipelines using Python" if the job description mentions "data pipelines"
- Reorder bullet points to put most relevant experience first
- Use synonyms that match the job description (e.g., "frontend development" vs "UI development")
- Keep all facts accurate while optimizing for relevance to the target job description
"""

_PROVIDER_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "together": "https://api.together.xyz/v1/chat/completions",
}
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-])\d{3,4}[ .-]\d{3,4}(?!\w)"
)
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,3}\s+"
    r"(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?)\b",
    re.I,
)
_ORG_SUFFIX_RE = re.compile(
    r"\b(?:[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3}\s+)"
    r"(?:Inc\.?|Incorporated|LLC|Ltd\.?|Limited|Corp\.?|Corporation|Company|Co\.?|"
    r"Technologies|Systems|Solutions|University|Institute|Group|Labs|Laboratories)\b",
)
_ORG_PREPOSITION_RE = re.compile(
    r"\b(?:at|for|with|from|joined)\s+((?:[A-Z][\w&.'-]*)(?:\s+[A-Z][\w&.'-]*){0,2})"
)
_ORG_TRAILING_WORDS = {"and", "or", "the", "a", "an", "to", "during", "while"}
_NUMBER_RE = re.compile(r"(?<![\w])\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|x|k|m|b)?(?![\w])", re.I)
_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "thousand": 1000,
    "million": 1000000,
    "billion": 1000000000,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
}


def _env_settings() -> tuple[str, str, str, str, int]:
    project_root = Path(__file__).resolve().parents[2]
    dotenv_path = project_root / ".env"
    file_values = dotenv_values(dotenv_path) if dotenv_path.exists() else {}

    def setting(name: str, default: str = "") -> str:
        value = os.getenv(name)
        if value is not None:
            return value.strip()
        return str(file_values.get(name) or "").strip() or default

    provider = setting("JOBMARKET_LLM_PROVIDER", "openai").casefold()
    model = setting("JOBMARKET_LLM_MODEL")
    base_url = setting("JOBMARKET_LLM_BASE_URL", _PROVIDER_ENDPOINTS.get(provider, ""))
    api_key = setting("JOBMARKET_LLM_API_KEY") or setting(
        f"{provider.upper().replace('-', '_')}_API_KEY"
    )
    timeout_value = setting("JOBMARKET_LLM_TIMEOUT", "60")
    try:
        timeout = int(timeout_value)
    except ValueError as error:
        raise TailoringError("JOBMARKET_LLM_TIMEOUT must be a whole number of seconds.") from error
    if not model:
        raise TailoringError(
            "Set JOBMARKET_LLM_MODEL in .env or the environment before tailoring a resume."
        )
    if not base_url:
        raise TailoringError(
            "Set JOBMARKET_LLM_BASE_URL for this provider; the endpoint must support OpenAI chat completions."
        )
    if not api_key:
        raise TailoringError(
            f"Set JOBMARKET_LLM_API_KEY or {provider.upper().replace('-', '_')}_API_KEY in .env."
        )
    if timeout <= 0:
        raise TailoringError("JOBMARKET_LLM_TIMEOUT must be greater than zero.")
    return provider, model, base_url, api_key, timeout


class OpenAICompatibleClient:
    """Small client for OpenAI Chat Completions-compatible provider endpoints."""

    def __init__(
        self, provider: str, model: str, base_url: str, api_key: str, timeout: int
    ) -> None:
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> OpenAICompatibleClient:
        return cls(*_env_settings())

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = requests.post(
                self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TailoringError("The LLM response did not contain JSON text.")
            return content
        except TailoringError:
            raise
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as error:
            raise TailoringError(
                f"LLM request failed or returned an unreadable response: {error}"
            ) from error


def _selected_source_bullets(
    resume: MasterResume, matcher_output: Mapping[str, Any]
) -> list[dict[str, str]]:
    source_bullets: dict[str, tuple[str, str]] = {}
    for experience in resume.experience:
        label = f"{experience.role} — {experience.company}"
        for bullet in experience.bullets:
            source_bullets[bullet.id] = (bullet.text, label)
    for project in resume.projects:
        for bullet in project.bullets:
            source_bullets[bullet.id] = (bullet.text, project.name)

    ranked = matcher_output.get("top_items", matcher_output.get("selected_bullets", [])) or []
    selected = []
    seen: set[str] = set()
    for item in ranked:
        if not isinstance(item, Mapping) or item.get("item_type") != "bullet":
            continue
        source_id = item.get("id") or item.get("source_id")
        if source_id in source_bullets and source_id not in seen:
            text, context = source_bullets[source_id]
            selected.append({"source_id": source_id, "context": context, "text": text})
            seen.add(source_id)
    return selected


def _safe_prompt_text(value: str, resume: MasterResume) -> str:
    """Remove contact-like strings from resume prose and job text before transmission."""
    text = _EMAIL_RE.sub("[redacted email]", value)
    text = _PHONE_RE.sub("[redacted phone]", text)
    text = _ADDRESS_RE.sub("[redacted address]", text)
    for contact_value in (resume.contact.email, resume.contact.phone, resume.contact.location):
        if contact_value:
            text = re.sub(re.escape(contact_value), "[redacted contact detail]", text, flags=re.I)
    return text


def _master_resume_facts(resume: MasterResume) -> str:
    parts = [
        resume.summary,
        *(skill for skills in resume.skills.values() for skill in skills),
        *(item.role for item in resume.experience),
        *(item.company for item in resume.experience),
        *(bullet.text for item in resume.experience for bullet in item.bullets),
        *(
            tag
            for item in resume.experience
            for bullet in item.bullets
            for tag in (bullet.tags or [])
        ),
        *(project.name for project in resume.projects),
        *(technology for project in resume.projects for technology in project.tech),
        *(bullet.text for project in resume.projects for bullet in project.bullets),
        *(
            tag
            for project in resume.projects
            for bullet in project.bullets
            for tag in (bullet.tags or [])
        ),
        *(
            metric
            for item in resume.experience
            for bullet in item.bullets
            for metric in (bullet.metrics or {}).values()
        ),
        *(
            metric
            for project in resume.projects
            for bullet in project.bullets
            for metric in (bullet.metrics or {}).values()
        ),
        *(item.institution for item in resume.education),
        *(item.degree for item in resume.education),
        *(item.field or "" for item in resume.education),
        *(item.name for item in resume.certifications),
        *(item.issuer or "" for item in resume.certifications),
    ]
    return " ".join(str(part) for part in parts if part is not None)


def _normalized_id(value: str) -> str:
    return " ".join(value.casefold().split())


def _contains_term(text: str, term: str) -> bool:
    normalized_term = _normalize_phrase(term)
    if normalized_term == "c":
        pattern = r"(?<!\w)c(?![\w+#])"
    elif normalized_term == "c++":
        pattern = r"(?<!\w)c\+\+(?![\w+#])"
    elif normalized_term == "c#":
        pattern = r"(?<!\w)c#(?![\w+#])"
    else:
        pattern = (
            r"(?<!\w)"
            + r"\s+".join(re.escape(part) for part in normalized_term.split())
            + r"(?!\w)"
        )
    return re.search(pattern, _normalize_phrase(text), re.I) is not None


def _skills_in(text: str) -> set[str]:
    normalized = _normalize_phrase(text)
    skills = extract_skills(normalized, "")
    return {_normalized_id(skill) for skill in skills if skill != TITLE_ONLY_MARKER}


def _tools_in(text: str) -> set[str]:
    normalized = _normalize_phrase(text)
    found: set[str] = set()
    for tool in TOOL_CATALOG:
        candidate = _normalize_phrase(tool)
        if candidate in {"c", "c++", "c#"}:
            pattern = r"(?<!\w)" + re.escape(candidate) + r"(?![\w+#])"
        else:
            pattern = (
                r"(?<!\w)" + r"\s+".join(re.escape(part) for part in candidate.split()) + r"(?!\w)"
            )
        if re.search(pattern, normalized, re.I):
            found.add(_normalized_id(candidate))
    return found


def _organization_names(resume: MasterResume) -> set[str]:
    names = {item.company for item in resume.experience}
    names.update(item.institution for item in resume.education)
    names.update(item.issuer for item in resume.certifications if item.issuer)
    names.update(project.name for project in resume.projects)
    return {_normalized_id(name) for name in names if name}


def _unknown_organizations(text: str, resume: MasterResume) -> list[str]:
    allowed_names = _organization_names(resume)
    allowed_skills = _skills_in(_master_resume_facts(resume))
    allowed_tools = _tools_in(_master_resume_facts(resume))
    candidates = [match.group(0) for match in _ORG_SUFFIX_RE.finditer(text)]
    for match in _ORG_PREPOSITION_RE.finditer(text):
        phrase = " ".join(match.group(1).split())
        phrase = " ".join(
            word
            for word in phrase.split()
            if word.casefold().strip(".,") not in _ORG_TRAILING_WORDS
        )
        normalized = _normalized_id(phrase)
        if (
            not normalized
            or normalized in allowed_skills
            or normalized in allowed_tools
            or _skills_in(phrase)
            or _tools_in(phrase)
        ):
            continue
        # Short product/technology names after a preposition are not employers.
        if len(normalized.split()) == 1 and normalized in {
            _normalized_id(skill) for skill in SKILL_CATALOG
        }:
            continue
        candidates.append(phrase)
    unique = []
    seen = set()
    for candidate in candidates:
        normalized = _normalized_id(candidate)
        if normalized not in allowed_names and normalized not in seen:
            unique.append(candidate)
            seen.add(normalized)
    return unique


def _number_facts(text: str) -> set[str]:
    numbers = {
        re.sub(r"[ ,]", "", match.group(0)).casefold() for match in _NUMBER_RE.finditer(text)
    }
    numbers.update(
        match.casefold()
        for match in re.findall(
            r"\b(?:" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\b", text, re.I
        )
    )
    return numbers


def _expected_gaps(matcher_output: Mapping[str, Any]) -> list[str]:
    value = matcher_output.get("missing_required_skills", [])
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return list(
            dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip())
        )
    return []


def validate_tailored_output(
    value: str | Mapping[str, Any],
    resume: MasterResume,
    matcher_output: Mapping[str, Any],
) -> TailoredOutput:
    """Parse strict output JSON and reject unsupported claims or source bullets."""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise TailoringError(
                f"LLM output must be strict JSON: {error.msg} at line {error.lineno}, column {error.colno}."
            ) from error
    else:
        decoded = value
    try:
        output = TailoredOutput.model_validate(decoded)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'output'}: {issue['msg']}"
            for issue in error.errors(include_url=False)
        )
        raise TailoringError(
            f"LLM output does not match the required JSON shape: {details}"
        ) from error
    if not isinstance(matcher_output, Mapping):
        raise TypeError("matcher_output must be a mapping")

    violations: list[str] = []
    selected = _selected_source_bullets(resume, matcher_output)
    allowed_ids = {item["source_id"] for item in selected}
    returned_ids = [item.source_id for item in output.selected_bullets]
    for source_id in returned_ids:
        if source_id not in allowed_ids:
            violations.append(f"source_id {source_id!r} was not selected by the matcher")
    if len(returned_ids) != len(set(returned_ids)):
        violations.append("selected_bullets contains duplicate source_id values")
    missing_ids = allowed_ids - set(returned_ids)
    if missing_ids:
        violations.append(
            f"rewrite every selected source bullet; missing ids: {', '.join(sorted(missing_ids))}"
        )

    expected_gaps = _expected_gaps(matcher_output)
    if set(output.gaps) != set(expected_gaps) or len(output.gaps) != len(set(output.gaps)):
        violations.append(f"gaps must list exactly the unmet required skills: {expected_gaps}")

    source_facts = _master_resume_facts(resume)
    allowed_skills = _skills_in(source_facts)
    allowed_tools = _tools_in(source_facts)
    allowed_numbers = _number_facts(source_facts)
    for bullet in output.selected_bullets:
        text = bullet.text
        added_skills = _skills_in(text) - allowed_skills
        if added_skills:
            violations.append(
                f"bullet {bullet.source_id!r} adds skills absent from the master resume: {', '.join(sorted(added_skills))}"
            )
        added_tools = _tools_in(text) - allowed_tools
        if added_tools:
            violations.append(
                f"bullet {bullet.source_id!r} adds tools absent from the master resume: {', '.join(sorted(added_tools))}"
            )
        for gap in expected_gaps:
            if _contains_term(text, gap):
                violations.append(
                    f"bullet {bullet.source_id!r} claims unmet required skill {gap!r}"
                )
        added_numbers = _number_facts(text) - allowed_numbers
        if added_numbers:
            violations.append(
                f"bullet {bullet.source_id!r} adds numbers absent from the master resume: {', '.join(sorted(added_numbers))}"
            )
        added_organizations = _unknown_organizations(text, resume)
        if added_organizations:
            violations.append(
                f"bullet {bullet.source_id!r} mentions employers or organizations absent from the master resume: "
                f"{', '.join(added_organizations)}"
            )

    if violations:
        raise TailoringError(
            "Tailored output failed fact validation: " + "; ".join(dict.fromkeys(violations))
        )
    return output


def _prompt_payload(
    resume: MasterResume,
    job_description: str,
    matcher_output: Mapping[str, Any],
    selected: list[dict[str, str]],
) -> dict[str, Any]:
    payload = {
        "master_resume": {
            "summary": _safe_prompt_text(resume.summary, resume),
            "skills": resume.skills,
            "experience": [
                {
                    "role": item.role,
                    "company": item.company,
                    "dates": item.dates.model_dump(),
                    "bullets": [
                        {
                            "id": bullet.id,
                            "text": _safe_prompt_text(bullet.text, resume),
                            "tags": bullet.tags or [],
                            "metrics": bullet.metrics or {},
                        }
                        for bullet in item.bullets
                    ],
                }
                for item in resume.experience
            ],
            "projects": [
                {
                    "name": project.name,
                    "tech": project.tech,
                    "bullets": [
                        {
                            "id": bullet.id,
                            "text": _safe_prompt_text(bullet.text, resume),
                            "tags": bullet.tags or [],
                            "metrics": bullet.metrics or {},
                        }
                        for bullet in project.bullets
                    ],
                }
                for project in resume.projects
            ],
            "education": [item.model_dump() for item in resume.education],
            "certifications": [item.model_dump() for item in resume.certifications],
        },
        "job_description": _safe_prompt_text(job_description, resume),
        "matcher_output": {
            "match_score": matcher_output.get("match_score"),
            "matched_skills": matcher_output.get("matched_skills", []),
            "missing_required_skills": _expected_gaps(matcher_output),
            "missing_preferred_skills": matcher_output.get("missing_preferred_skills", []),
        },
        "selected_bullets": selected,
    }
    return _redact_contact_values(payload, resume)


def _redact_contact_values(value: Any, resume: MasterResume) -> Any:
    if isinstance(value, str):
        return _safe_prompt_text(value, resume)
    if isinstance(value, list):
        return [_redact_contact_values(item, resume) for item in value]
    if isinstance(value, dict):
        return {key: _redact_contact_values(item, resume) for key, item in value.items()}
    return value


def tailor_resume(
    resume: MasterResume,
    job_description: str,
    matcher_output: Mapping[str, Any],
    *,
    client: CompletionClient | None = None,
) -> TailoredOutput:
    """Rewrite matcher-selected bullets, retrying once after factual violations."""
    if not isinstance(resume, MasterResume):
        raise TypeError("resume must be a MasterResume instance")
    if not isinstance(job_description, str):
        raise TypeError("job_description must be a string")
    if not isinstance(matcher_output, Mapping):
        raise TypeError("matcher_output must be a mapping")

    selected = _selected_source_bullets(resume, matcher_output)
    expected_gaps = _expected_gaps(matcher_output)
    if not selected:
        return TailoredOutput(gaps=expected_gaps)
    completion_client = client or OpenAICompatibleClient.from_environment()
    payload = _prompt_payload(resume, job_description, matcher_output, selected)
    user_prompt = json.dumps(payload, ensure_ascii=False)
    previous_violations: list[str] = []

    for attempt in range(2):
        if previous_violations:
            user_prompt = json.dumps(
                {
                    **payload,
                    "validation_violations_to_fix": previous_violations,
                    "instruction": "Correct these violations using only the supplied master-resume facts; return strict JSON again.",
                },
                ensure_ascii=False,
            )
        raw_output = completion_client.complete(SYSTEM_PROMPT, user_prompt)
        try:
            return validate_tailored_output(raw_output, resume, matcher_output)
        except TailoringError as error:
            previous_violations = [str(error)]
            if attempt == 1:
                raise TailoringError(
                    f"LLM output remained invalid after one retry: {error}"
                ) from error
    raise TailoringError("LLM did not return a validated result.")
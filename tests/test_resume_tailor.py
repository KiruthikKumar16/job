import json
from pathlib import Path

import pytest
from jobmarket.resume import MasterResume
from jobmarket.resume import tailor as tailor_module
from jobmarket.resume.tailor import (
    OpenAICompatibleClient,
    TailoringError,
    tailor_resume,
    validate_tailored_output,
)

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def sample_resume():
    return MasterResume.model_validate(
        {
            "contact": {
                "name": "Private Candidate",
                "email": "private@example.com",
                "phone": "+1-555-0100",
                "location": "123 Main Street, Springfield",
            },
            "summary": "Data engineer with Python and SQL experience.",
            "skills": {"Languages and tools": ["Python", "SQL", "AWS"]},
            "experience": [
                {
                    "role": "Data Engineer",
                    "company": "Example Systems",
                    "dates": {"start": "2022-04", "end": "Present"},
                    "bullets": [
                        {
                            "id": "exp-a",
                            "text": "Built Python and SQL pipelines on AWS, cutting latency by 35%.",
                            "tags": ["Python", "SQL", "AWS"],
                            "metrics": {"latency_reduction": "35%"},
                        }
                    ],
                }
            ],
        }
    )


def matcher_output():
    return {
        "match_score": 72,
        "matched_skills": ["Python", "SQL"],
        "missing_required_skills": ["Kubernetes"],
        "missing_preferred_skills": [],
        "top_items": [{"item_type": "bullet", "id": "exp-a", "relevance_score": 80}],
    }


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("tailored_invented_skill.json", "adds skills absent"),
        ("tailored_invented_employer.json", "employers or organizations absent"),
        ("tailored_invented_number.json", "adds numbers absent"),
        ("tailored_invented_tool.json", "adds tools absent"),
    ],
)
def test_validator_rejects_fabricated_outputs(fixture, message):
    with pytest.raises(TailoringError, match=message):
        validate_tailored_output(read_fixture(fixture), sample_resume(), matcher_output())


def test_validator_accepts_supported_rewrite_and_expected_gap():
    output = validate_tailored_output(
        read_fixture("tailored_valid.json"), sample_resume(), matcher_output()
    )
    assert output.selected_bullets[0].source_id == "exp-a"
    assert output.gaps == ["Kubernetes"]


class MockClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


def test_tailoring_retries_once_and_includes_violations():
    client = MockClient(
        [
            read_fixture("tailored_invented_skill.json"),
            read_fixture("tailored_valid.json"),
        ]
    )
    result = tailor_resume(
        sample_resume(), "Need Python, SQL, and Kubernetes.", matcher_output(), client=client
    )
    assert result.selected_bullets[0].source_id == "exp-a"
    assert len(client.calls) == 2
    assert "skills absent from the master resume" in client.calls[1][1]
    assert "using only the supplied master-resume facts" in client.calls[1][0]


def test_prompt_excludes_contact_details():
    client = MockClient([read_fixture("tailored_valid.json")])
    tailor_resume(sample_resume(), "Python and SQL role.", matcher_output(), client=client)
    prompt = client.calls[0][1]
    assert "private@example.com" not in prompt
    assert "+1-555-0100" not in prompt
    assert "123 Main Street" not in prompt
    assert "Private Candidate" not in prompt


def test_gaps_must_equal_matcher_unmet_required_skills():
    payload = json.loads(read_fixture("tailored_valid.json"))
    payload["gaps"] = []
    with pytest.raises(TailoringError, match="gaps must list exactly"):
        validate_tailored_output(payload, sample_resume(), matcher_output())


def test_no_selected_bullets_skips_llm_and_returns_gaps():
    class UnexpectedClient:
        def complete(self, *args):
            pytest.fail("LLM should not be called when there are no selected bullets")

    result = tailor_resume(
        sample_resume(),
        "Any role",
        {"missing_required_skills": ["Kubernetes"], "top_items": []},
        client=UnexpectedClient(),
    )
    assert result.selected_bullets == []
    assert result.gaps == ["Kubernetes"]


def test_provider_settings_load_from_environment_and_request_has_timeout(monkeypatch):
    monkeypatch.setattr(tailor_module, "dotenv_values", lambda path: {})
    for name in (
        "JOBMARKET_LLM_PROVIDER",
        "JOBMARKET_LLM_MODEL",
        "JOBMARKET_LLM_BASE_URL",
        "JOBMARKET_LLM_API_KEY",
        "GROQ_API_KEY",
        "JOBMARKET_LLM_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JOBMARKET_LLM_PROVIDER", "groq")
    monkeypatch.setenv("JOBMARKET_LLM_MODEL", "configured-model")
    monkeypatch.setenv("JOBMARKET_LLM_API_KEY", "test-only-key")
    monkeypatch.setenv("JOBMARKET_LLM_TIMEOUT", "17")
    client = OpenAICompatibleClient.from_environment()
    assert client.model == "configured-model"
    assert client.provider == "groq"
    assert client.timeout == 17
    assert client.base_url == tailor_module._PROVIDER_ENDPOINTS["groq"]

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"selected_bullets":[],"gaps":[]}'}}]}

    calls = []

    def mock_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(tailor_module.requests, "post", mock_post)
    assert client.complete("system", "user") == '{"selected_bullets":[],"gaps":[]}'
    assert calls[0][0] == tailor_module._PROVIDER_ENDPOINTS["groq"]
    assert calls[0][1]["timeout"] == 17
    assert calls[0][1]["json"]["model"] == "configured-model"

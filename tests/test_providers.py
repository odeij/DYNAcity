import json
from types import SimpleNamespace

import pytest

from dynacity.providers import (
    INVALID_OUTPUT,
    REFUSED,
    TRUNCATED,
    AnthropicProvider,
    GeminiProvider,
    _inline_refs,
    resolve_provider,
)
from dynacity.scenario_compiler import ScenarioDraft, compile_scenario

from test_scenario_compiler import REQUEST, draft, snapshot

pytest.importorskip("google.genai")


class FakeGemini:
    """Stands in for google.genai.Client: returns queued JSON texts, records calls."""

    def __init__(self, *texts, finish="STOP", block=None):
        self.texts, self.calls = list(texts), []
        self.finish, self.block = finish, block
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            prompt_feedback=SimpleNamespace(block_reason=self.block) if self.block else None,
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=self.finish))],
            text=self.texts.pop(0) if self.texts else None,
        )


def test_gemini_draft_goes_through_the_same_checker():
    fake = FakeGemini(draft().model_dump_json())
    result = compile_scenario(REQUEST, snapshot(), provider=GeminiProvider(fake, "gemini-test"))
    assert result.ok and result.provider == "gemini" and result.model == "gemini-test"
    assert result.scenario.interventions[0].object_ids == ["1", "2"]
    config = fake.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert "$ref" not in json.dumps(config.response_json_schema)
    assert "Mar Mikhael" in fake.calls[0]["contents"] and "lidar" not in fake.calls[0]["contents"]


def test_gemini_invented_source_is_repaired_like_claude():
    bad = draft(effect_source="World Bank RDNA 2020").model_dump_json()
    fake = FakeGemini(bad, draft().model_dump_json())
    result = compile_scenario(REQUEST, snapshot(), provider=GeminiProvider(fake))
    assert [a.report.ok for a in result.attempts] == [False, True]
    assert "effect.source_not_in_request" in fake.calls[1]["contents"]


@pytest.mark.parametrize(
    "fake, code",
    [
        (FakeGemini("{}", finish="SAFETY"), REFUSED),
        (FakeGemini("{}", block="OTHER"), REFUSED),
        (FakeGemini('{"scenario_id": "x"', finish="MAX_TOKENS"), TRUNCATED),
        (FakeGemini('{"scenario_id": "x"}'), INVALID_OUTPUT),
    ],
)
def test_gemini_failures_become_named_hard_issues(fake, code):
    result = compile_scenario(REQUEST, snapshot(), provider=GeminiProvider(fake), max_repairs=0)
    assert not result.ok
    assert [i.code for i in result.attempts[0].report.issues] == [code]


def test_gemini_falls_back_when_model_is_overloaded():
    from google.genai import errors

    fake = FakeGemini(draft().model_dump_json())
    answer = fake.models.generate_content

    def busy_first(**kwargs):
        if kwargs["model"] == "gemini-busy":
            raise errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
        return answer(**kwargs)

    fake.models.generate_content = busy_first
    provider = GeminiProvider(fake, "gemini-busy", fallback_models=("gemini-spare",))
    result = compile_scenario(REQUEST, snapshot(), provider=provider)
    assert result.ok and result.attempts[0].model == "gemini-spare"


def test_every_model_unavailable_is_a_named_issue_not_a_crash():
    import httpx
    from google.genai import errors

    fake = FakeGemini()
    calls = []
    def down(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "gemini-a":
            raise errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
        raise httpx.ConnectError("EOF occurred in violation of protocol")
    fake.models.generate_content = down
    result = compile_scenario(REQUEST, snapshot(), provider=GeminiProvider(fake, "gemini-a", fallback_models=("gemini-b",)))
    assert calls == ["gemini-a", "gemini-b"]
    issue = result.attempts[0].report.issues[0]
    assert not result.ok and issue.code == "model.unavailable" and "try again" in issue.message


def test_gemini_does_not_fall_back_on_bad_requests():
    from google.genai import errors

    fake = FakeGemini()
    def bad_request(**kwargs):
        raise errors.ClientError(400, {"error": {"code": 400, "message": "bad schema", "status": "INVALID_ARGUMENT"}})
    fake.models.generate_content = bad_request
    with pytest.raises(errors.ClientError):
        compile_scenario(REQUEST, snapshot(), provider=GeminiProvider(fake, "gemini-a", fallback_models=("gemini-b",)))


def test_inline_refs_removes_definitions():
    schema = _inline_refs(ScenarioDraft.model_json_schema())
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    lever = schema["properties"]["interventions"]["items"]
    assert "selector" in lever["properties"]


def test_resolve_provider_prefers_explicit_then_env_then_available_key(monkeypatch):
    for var in ("DYNACITY_LLM_PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert resolve_provider().name == "gemini"
    assert resolve_provider(model="gemini-3.5-flash-lite").model == "gemini-3.5-flash-lite"
    monkeypatch.setenv("DYNACITY_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert isinstance(resolve_provider(), AnthropicProvider)
    with pytest.raises(ValueError):
        resolve_provider("deepseek")

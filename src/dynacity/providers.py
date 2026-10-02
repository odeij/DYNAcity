"""Language-model providers for the scenario compiler.

The compiler needs exactly one thing from a model: given instructions and a
message, return a filled-in draft that parses against a pydantic schema, or say
why it could not. Everything that decides what is true — scope, effect sizes,
provenance — happens afterwards in deterministic code, so providers are
interchangeable and a weaker model only costs more rejected attempts, never a
wrong scenario.

Keys are read from the environment by each SDK and never stored here:
`ANTHROPIC_API_KEY` for Anthropic, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) for
Gemini. `DYNACITY_LLM_PROVIDER` picks one explicitly; otherwise the first
provider whose key is set is used.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# Failure codes surfaced as hard validation issues on the attempt.
REFUSED = "model.refusal"
TRUNCATED = "model.truncated"
NO_OUTPUT = "model.no_output"
INVALID_OUTPUT = "model.invalid_output"
# Overloaded, rate-limited, or unreachable after the SDK's retries and any
# fallback models: nothing is wrong with the request, so try again later.
UNAVAILABLE = "model.unavailable"

FAILURE_MESSAGES = {
    REFUSED: "the model declined the request",
    TRUNCATED: "the model's reply was cut off before the draft was complete",
    NO_OUTPUT: "the model returned no draft",
    INVALID_OUTPUT: "the model's reply did not match the draft form",
    UNAVAILABLE: "the model service is busy or unreachable; try again in a minute",
}

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    # Google's recommended default for new projects (September 2026); the
    # cheaper option is gemini-3.5-flash-lite.
    "gemini": "gemini-3.8-flash",
}
KEY_VARIABLES = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


class DraftProvider(Protocol):
    name: str
    model: str

    def propose(self, system: str, content: str, output: type[T]) -> tuple[T | None, str | None]:
        ...


def _missing_sdk(extra: str, package: str) -> RuntimeError:
    return RuntimeError(f"this provider needs {package}: pip install 'dynacity-forecasting[{extra}]'")


class AnthropicProvider:
    name = "anthropic"
    # Server-side refusal fallback: a declined request is re-run on Anthropic's
    # recommended substitute inside the same call instead of failing the compile.
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, client: Any | None = None, model: str | None = None):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - only without the extra
                raise _missing_sdk("llm", "the Anthropic SDK") from exc
            client = anthropic.Anthropic()
        self.client = client
        self.model = model or DEFAULT_MODELS["anthropic"]

    @staticmethod
    def _unavailable(exc: Exception) -> bool:
        try:
            import anthropic
        except ImportError:  # pragma: no cover - fakes in tests never raise SDK errors
            return False
        if isinstance(exc, anthropic.APIConnectionError):
            return True
        return isinstance(exc, anthropic.APIStatusError) and (exc.status_code == 429 or exc.status_code >= 500)

    def propose(self, system: str, content: str, output: type[T]) -> tuple[T | None, str | None]:
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=output,
                betas=[self.FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as exc:
            if self._unavailable(exc):
                return None, UNAVAILABLE
            raise
        if response.stop_reason == "refusal":
            return None, REFUSED
        if response.stop_reason == "max_tokens":
            return None, TRUNCATED
        if response.parsed_output is None:
            return None, NO_OUTPUT
        return response.parsed_output, None


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve pydantic's `$defs`/`$ref` into one self-contained schema.

    Keeps the schema sent to Gemini free of references, which its
    structured-output subset handles less reliably than inline objects.
    """

    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = copy.deepcopy(defs[node["$ref"].split("/")[-1]])
                return resolve({**target, **{k: v for k, v in node.items() if k != "$ref"}})
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


class GeminiProvider:
    name = "gemini"
    _BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}
    # Tried in order when the chosen model stays overloaded or rate-limited
    # after the SDK's own retries (Gemini returns 503 "high demand" at peaks).
    FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-3.5-flash")
    _UNAVAILABLE = {429, 500, 502, 503, 504}

    def __init__(
        self, client: Any | None = None, model: str | None = None, fallback_models: tuple[str, ...] | None = None
    ):
        if client is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:  # pragma: no cover - only without the extra
                raise _missing_sdk("gemini", "the Google Gen AI SDK") from exc
            client = genai.Client(http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(
                attempts=4, initial_delay=2.0, max_delay=20.0, http_status_codes=sorted(self._UNAVAILABLE | {408}),
            )))
        self.client = client
        self.model = model or DEFAULT_MODELS["gemini"]
        chain = self.FALLBACK_MODELS if fallback_models is None else fallback_models
        self.models = (self.model, *[m for m in chain if m != self.model])
        # The model that produced the most recent reply, for the audit trail.
        self.last_model = self.model

    def _generate(self, content: str, config: Any) -> Any | None:
        """The first reply from the model chain, or None if every model is unavailable."""

        import httpx
        from google.genai import errors

        for model in self.models:
            try:
                response = self.client.models.generate_content(model=model, contents=content, config=config)
                self.last_model = model
                return response
            except errors.APIError as exc:
                if getattr(exc, "code", None) not in self._UNAVAILABLE:
                    raise
            except httpx.TransportError:
                # Dropped connections (seen live as SSL EOFs) are not the
                # request's fault; the next model is effectively a retry.
                pass
            self.last_model = model
        return None

    def propose(self, system: str, content: str, output: type[T]) -> tuple[T | None, str | None]:
        from google.genai import types

        response = self._generate(content, types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=_inline_refs(output.model_json_schema()),
            max_output_tokens=16000,
        ))
        if response is None:
            return None, UNAVAILABLE
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            return None, REFUSED
        candidates = getattr(response, "candidates", None) or []
        finish = getattr(getattr(candidates[0], "finish_reason", None), "name", "") if candidates else ""
        if finish in self._BLOCKED:
            return None, REFUSED
        if finish == "MAX_TOKENS":
            return None, TRUNCATED
        text = getattr(response, "text", None)
        if not text:
            return None, NO_OUTPUT
        # Validated by our own schema rather than trusted from the SDK, so both
        # providers are held to exactly the same contract.
        try:
            return output.model_validate_json(text), None
        except ValidationError:
            return None, INVALID_OUTPUT


PROVIDERS = {"anthropic": AnthropicProvider, "gemini": GeminiProvider}


def resolve_provider(name: str | None = None, model: str | None = None) -> DraftProvider:
    """Pick a provider: explicit name, then `DYNACITY_LLM_PROVIDER`, then whichever key is set."""

    name = name or os.environ.get("DYNACITY_LLM_PROVIDER")
    if name is None:
        name = next(
            (p for p, keys in KEY_VARIABLES.items() if any(os.environ.get(k) for k in keys)),
            "anthropic",
        )
    if name not in PROVIDERS:
        raise ValueError(f"unknown provider {name!r}; choose from {sorted(PROVIDERS)}")
    return PROVIDERS[name](model=model)

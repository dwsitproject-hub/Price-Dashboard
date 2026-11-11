"""Claude API access for the intelligence sections (AI narrative, News extraction, event Calendar).

One place for the model, credentials, refusal fallback, streaming and stop-reason handling. Requests are
streamed (long documents and web searches can run for minutes) and read with get_final_message().
Callers get plain text or JSON validated against a JSON schema (structured outputs); every failure is a
ClaudeError with a message that is safe to show to an administrator.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic

from app.core.config import get_settings

logger = logging.getLogger("app.claude")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PAUSE_RESUMES = 4          # server-tool loops (web search) that stop with pause_turn


class ClaudeError(Exception):
    pass


@dataclass
class ClaudeResult:
    text: str
    model: str
    usage: dict = field(default_factory=dict)
    data: Any = None


def _client() -> anthropic.Anthropic:
    s = get_settings()
    if not s.claude_configured:
        raise ClaudeError("Claude is not configured (ANTHROPIC_API_KEY is not set).")
    return anthropic.Anthropic(api_key=s.anthropic_api_key, max_retries=3)


def _usage(msg) -> dict:
    u = msg.usage
    return {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None) or 0}


def run(*, system: str, content: list[dict] | str, max_tokens: int = 32000, effort: str = "medium",
        schema: dict | None = None, tools: list[dict] | None = None) -> ClaudeResult:
    """One request (plus pause_turn resumes for server tools). Returns the final text, or parsed JSON in .data
    when `schema` is given."""
    s = get_settings()
    client = _client()
    messages: list[dict] = [{"role": "user", "content": content}]
    output_config: dict = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kwargs: dict = {"model": s.claude_model, "max_tokens": max_tokens, "system": system,
                    "output_config": output_config}
    if tools:
        kwargs["tools"] = tools
    if s.claude_fallbacks:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"

    usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
    try:
        for _ in range(MAX_PAUSE_RESUMES + 1):
            with client.beta.messages.stream(messages=messages, **kwargs) as stream:
                msg = stream.get_final_message()
            for k, v in _usage(msg).items():
                usage_total[k] += v
            if msg.stop_reason == "pause_turn":   # long server-tool turn: send its content back to resume it
                messages.append({"role": "assistant", "content": msg.content})
                continue
            break
    except anthropic.AuthenticationError as exc:
        raise ClaudeError("The Anthropic API key was rejected.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise ClaudeError("The Anthropic API key may not use this model or feature.") from exc
    except anthropic.RateLimitError as exc:
        raise ClaudeError("Claude rate limit reached; try again later.") from exc
    except anthropic.BadRequestError as exc:
        raise ClaudeError(f"Claude rejected the request: {exc.message}"[:500]) from exc
    except anthropic.APIStatusError as exc:
        raise ClaudeError(f"Claude API error {exc.status_code}; try again later.") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeError("Cannot reach api.anthropic.com from the backend server.") from exc

    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        raise ClaudeError(f"Claude declined this request (category: {cat or 'unspecified'}).")
    if msg.stop_reason == "max_tokens":
        raise ClaudeError("Claude's answer was cut off at the output limit; the input may be too large.")
    if msg.stop_reason == "pause_turn":
        raise ClaudeError("Claude's web search did not finish in time; try again.")

    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    result = ClaudeResult(text=text, model=getattr(msg, "model", s.claude_model), usage=usage_total)
    if schema is not None:
        try:
            result.data = json.loads(text)
        except ValueError as exc:
            raise ClaudeError("Claude returned output that is not valid JSON.") from exc
    logger.info("claude call ok model=%s in=%s out=%s", result.model, usage_total["input_tokens"],
                usage_total["output_tokens"])
    return result

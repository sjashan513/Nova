"""
core/llm/nim_client.py — Capa 1 of the Fase 3 Worker layer
(NOVA_WORKER_LAYER_ADR.md §2.3).

Two pure functions, no class, no state. call_nim() covers exactly what
is identical across every model NVIDIA NIM serves through this same
OpenAI-compatible endpoint: auth, endpoint, payload construction,
sending the request. parse_json_response() covers a second piece of
genuinely shared behavior across the 5 LLM-powered Fase 3 workers:
parsing a model's raw text response as JSON and checking the expected
keys are present -- the SAME operation in all 5 cases (try json.loads,
check required keys), with only the list of keys varying, which is why
it's a shared function rather than five near-identical try/except
blocks (contrast with worker_ts_fix vs worker_jsdoc's *result* shapes,
which look similar today by coincidence, not by shared concern -- see
NOVA_WORKER_LAYER_ADR.md session notes on why those stayed separate).

Neither function imports from core.domain -- this module stays a leaf
dependency, same direction rule the rest of the codebase follows
(NOVA_CLI_MVP_ROADMAP.md §0.2). Parsing failures raise plain
ValueError, not a NovaError subtype: deciding what a parse failure
MEANS for a specific worker (give up? retry with a corrective prompt?
something else) is each worker's own job inside its own execute() --
same principle as call_nim's network errors propagating unwrapped.
"""

import json
import os
import re
from typing import Any, Dict, List

import requests

_NIM_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"

# Defaults for the two payload fields Jashan's examples show varying
# by model (top_p: 0.95 for minimax-m3, 1 for glm-5.1) that were NOT
# part of the explicit call_nim signature this session asked for
# (model, system_prompt, user_content, temperature, thinking). Exposed
# as optional parameters with a sensible default rather than hardcoded,
# so a Worker that needs a different value isn't blocked -- but kept
# out of the required signature since no specific value was requested
# for them yet.
_DEFAULT_TOP_P = 0.95
_DEFAULT_MAX_TOKENS = 8192
_DEFAULT_TIMEOUT_SECONDS = 60


def call_nim(
    model: str,
    system_prompt: str,
    user_content: str,
    temperature: float,
    thinking: bool = False,
    top_p: float = _DEFAULT_TOP_P,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """
    Calls NVIDIA NIM's OpenAI-compatible chat completions endpoint and
    returns the model's raw text response. Always non-streaming
    (stream=False is hardcoded, not exposed as a parameter) -- a
    Worker needs a final string to parse into its own result shape,
    not a token stream; if a future Worker genuinely needs streaming,
    that is a real case for a second function, not a parameter here
    (same anticipation rule as everywhere else this session).

    thinking is accepted but NOT YET wired into the payload. Neither
    of the two real NIM examples checked this session (minimax-m3,
    glm-5.1) show a `thinking` field anywhere -- not at the payload's
    top level, not in any extra_body. Wiring an unconfirmed field name
    risks NIM silently ignoring it (worse: false confidence that
    something is controlled when it isn't) or rejecting the request
    outright. Until the real field name/support is confirmed for the
    specific model in use, this parameter is accepted for signature
    stability (no Worker call site needs to change later) but has no
    effect on the request sent.

    Auth: reads NIM_API_KEY from the environment (.env, per project
    convention -- same variable core/planner/planner.py already uses
    for the Planner's own NIM call). Raises RuntimeError immediately,
    before making any request, if it is not set -- fails loud at the
    one place that would otherwise fail confusingly deep inside
    `requests` with a 401.

    Does not catch anything. A non-2xx response raises via
    raise_for_status() (requests.exceptions.HTTPError). A network
    failure (timeout, connection error) raises directly from
    `requests`. A response body that doesn't match the expected
    {"choices": [{"message": {"content": ...}}]} shape raises
    KeyError/IndexError. All of these propagate to the caller (a
    Worker's execute()) completely unwrapped -- see module docstring.

    Returns:
        The model's response text (choices[0].message.content),
        exactly as NIM returned it -- no parsing, no stripping, no
        interpretation. Each Worker's execute() decides what to do
        with this raw string.
    """
    api_key = os.environ.get("NIM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "NIM_API_KEY is not set in the environment. Check .env."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": False,
    }

    response = requests.post(
        _NIM_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()

    return response.json()["choices"][0]["message"]["content"]


_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def _strip_markdown_fence(text: str) -> str:
    """
    Strips a single leading/trailing markdown code fence (```json ...
    ``` or plain ``` ... ```) if present. Despite an explicit "JSON
    only, no markdown fences" instruction in a system prompt, models
    sometimes wrap their response in fences anyway -- handling this
    once, here, means every worker gets that tolerance for free
    instead of either re-implementing the same strip logic five times,
    or failing on a formatting quirk that has nothing to do with that
    worker's actual task.
    """
    stripped = text.strip()
    match = _FENCE_PATTERN.match(stripped)
    return match.group(1).strip() if match else stripped


def parse_json_response(raw: str, required_keys: List[str]) -> Dict[str, Any]:
    """
    Parses `raw` (a model's raw text response, typically straight from
    call_nim) as a JSON object and verifies every key in
    `required_keys` is present. Shared across the 5 LLM-powered Fase 3
    workers -- this exact operation (parse, check keys) is identical
    in all 5 cases; only the list of keys differs, which is why this
    is a parameter rather than five separate near-identical functions.

    Does NOT translate failures into a NovaError subtype -- raises
    plain ValueError with a message describing exactly what went
    wrong. Deciding what a parse failure MEANS for a given worker
    (give up and return status: "error"? retry with a corrective
    prompt of its own? something else) is each worker's own job, the
    same way a network error from call_nim is.

    Raises:
        ValueError: `raw` is not valid JSON after fence-stripping,
            parses to something other than a JSON object, or is
            missing one or more of `required_keys`.
    """
    text = _strip_markdown_fence(raw)

    try:
        parsed = json.loads(text, strict=False)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Response is not valid JSON: {e}. Raw response: {raw!r}"
        ) from e

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Response parsed as JSON but is not a JSON object (got "
            f"{type(parsed).__name__}). Raw response: {raw!r}"
        )

    missing = [k for k in required_keys if k not in parsed]
    if missing:
        raise ValueError(
            f"Response is missing required key(s): {', '.join(missing)}. "
            f"Parsed response: {parsed!r}"
        )

    return parsed

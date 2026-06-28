"""
core/llm/nim_client.py — Capa 1 of the Fase 3 Worker layer
(NOVA_WORKER_LAYER_ADR.md §2.3).

A single pure function, no class, no state. Covers exactly what is
identical across every model NVIDIA NIM serves through this same
OpenAI-compatible endpoint: auth, endpoint, payload construction,
sending the request. It does NOT cover parsing the CONTENT of a
response into a worker-specific result shape -- that stays inside each
Worker's own execute(), by design (see the ADR: "parsing is different
per consumer... that part stays separate").

Confirmed payload shape (verified against two real NIM model examples,
not assumed from memory): minimax/minimax-m3 and z-ai/glm-5.1 both hit
the same endpoint with the same {model, messages, temperature, top_p,
max_tokens, stream} shape -- the OpenAI SDK used in one example is
syntactic sugar over the exact same POST the other makes explicit with
`requests`. This means a single function, no per-model branching, is
still correct for Fase 3 -- see the ADR's anticipation discussion: if
a genuinely different API shape (not just a different model string)
shows up in the future, THAT is a real case for revisiting this as a
class with internal dispatch, not before.

Deliberately does NOT catch or wrap any failure -- network errors
(requests.exceptions.*), a non-2xx HTTP response (raise_for_status),
or a malformed response body (KeyError/IndexError on parsing) all
propagate unmodified. This is a pure transport function; deciding what
a given failure MEANS for a specific worker (retry internally? give up
and return status: "error"? something else entirely) is explicitly
each Worker's own job, inside its own execute() -- "cada worker es un
ecosistema propio con sus reglas de policy error" (session decision,
Fase 3).
"""

import os
from typing import Optional

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

    Auth: reads NIM_API_KEY_ENV_VAR from the environment (.env, per project
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
    api_key = os.environ.get("NIM_API_KEY_ENV_VAR")
    if not api_key:
        raise RuntimeError(
            "NIM_API_KEY_ENV_VAR is not set in the environment. Check .env."
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

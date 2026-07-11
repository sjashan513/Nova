"""
core/llm/openai_client.py — OpenAI API client for Nova.

Mirrors nim_client.py in structure and philosophy: two pure functions,
no class, no state. call_openai() covers what is identical across every
OpenAI model call: auth, endpoint, payload construction, sending the
request. parse_json_response() is shared with nim_client — import it
from there rather than duplicating it here.

Current callers:
  memory/bibliotecario/extractor.py — GPT-4o-mini for entity extraction

Same rules as nim_client.py:
  - Does not catch anything. All errors propagate unwrapped to the caller.
  - Auth reads OPENAI_API_KEY from the environment. Fails loud immediately
    if not set, before making any request.
  - No streaming — callers need a final string to parse, not a token stream.
  - thinking parameter accepted for signature stability, not yet wired —
    same reasoning as nim_client.py (unconfirmed field name per model).

Same dependency rule: this module stays a leaf. No imports from
core.domain, no imports from workers or tools.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

import requests

_OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"

_DEFAULT_TOP_P = 1.0
_DEFAULT_MAX_TOKENS = 1000
_DEFAULT_TIMEOUT_SECONDS = 30


def call_openai(
    model: str,
    system_prompt: str,
    user_content: str,
    temperature: float,
    top_p: float = _DEFAULT_TOP_P,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    response_format: Optional[str] = None,
) -> str:
    """
    Calls the OpenAI chat completions endpoint and returns the model's
    raw text response.

    response_format: optional — pass "json_object" to enable OpenAI's
    native JSON mode (forces the model to return valid JSON). Only
    supported by models that declare it (gpt-4o-mini, gpt-4o, etc.).
    Not all callers need this — the Extractor does, because it always
    expects JSON back from GPT-4o-mini.

    Auth: reads OPENAI_API_KEY from the environment. Raises RuntimeError
    immediately if not set — same pattern as nim_client.py's NIM_API_KEY
    check.

    Does not catch anything. A non-2xx response raises via
    raise_for_status(). Network failures raise directly from requests.
    A malformed response body raises KeyError/IndexError. All propagate
    unwrapped to the caller — the caller decides what a failure means.

    Returns:
        The model's response text (choices[0].message.content), exactly
        as OpenAI returned it — no parsing, no stripping. Each caller
        decides what to do with the raw string.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set in the environment. Check .env."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
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

    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}

    response = requests.post(
        _OPENAI_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()

    return response.json()["choices"][0]["message"]["content"]

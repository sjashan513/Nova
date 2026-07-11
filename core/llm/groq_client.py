"""
core/llm/groq_client.py — Groq API client for Nova.

Mirrors openai_client.py and nim_client.py in structure and philosophy:
pure functions, no class, no state. call_groq() covers what is identical
across every Groq call: auth, endpoint, payload construction, sending
the request.

Current callers:
  cli.py — Qwen conversational layer with optional tool calling

Same rules as nim_client.py and openai_client.py:
  - Does not catch anything. All errors propagate unwrapped to the caller.
  - Auth reads GROQ_API_KEY from the environment. Fails loud immediately
    if not set, before making any request.
  - No streaming — callers need a final message to process, not a token stream.

Tool calling:
  call_groq() accepts an optional tools list (OpenAI-compatible schema).
  Returns the full message dict from choices[0].message so the caller
  can inspect both content and tool_calls without re-parsing.
  The caller (cli.py) decides what to do with tool_calls — groq_client
  never executes tools, never makes a second call, never accumulates history.
"""

import os
from typing import Any, Dict, List, Optional

import requests

_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

_DEFAULT_MODEL = "openai/gpt-oss-120b"
_DEFAULT_TEMPERATURE = 1.0
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_TIMEOUT_SECONDS = 30


_DEFAULT_TOP_P = 1.0


def call_groq(
    messages: List[Dict[str, Any]],
    model: str = _DEFAULT_MODEL,
    temperature: float = _DEFAULT_TEMPERATURE,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    top_p: float = _DEFAULT_TOP_P,
    tools: Optional[List[Dict[str, Any]]] = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    Calls the Groq chat completions endpoint and returns the raw message
    dict from choices[0].message.

    Returns the full message dict so the caller can inspect:
      - message["content"]     → str | None  (text response)
      - message["tool_calls"]  → list | None  (tool call requests)

    tools: optional list of OpenAI-compatible tool schemas. When provided,
    Groq may return tool_calls instead of (or alongside) content.
    tool_choice is left unset — Groq defaults to "auto", letting the
    model decide when to invoke a tool.

    Auth: reads GROQ_API_KEY from the environment. Raises RuntimeError
    immediately if not set.

    Does not catch anything. A non-2xx response raises via
    raise_for_status(). Network failures raise directly from requests.
    A malformed response body raises KeyError/IndexError. All propagate
    unwrapped to the caller.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set in the environment. Check .env."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": False,
    }

    if tools:
        payload["tools"] = tools

    response = requests.post(
        _GROQ_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()

    return response.json()["choices"][0]["message"]

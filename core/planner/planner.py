"""
Planner — real call to the Planner LLM via NVIDIA NIM (OpenAI-compatible SDK).

Uses the openai SDK instead of raw requests -- NIM's recommended client,
handles retries, timeouts, and auth headers cleanly without manual payload
construction. The model string is the only config point; swapping models
never requires touching the call logic.

Two-step parsing design (unchanged from the requests version):
  1. HTTP + JSON extraction  → PlannerResponseError on failure
  2. Pydantic instantiation  → PlannerValidationError on schema mismatch
"""

import json
import os
import re
from typing import Any, Dict, Optional

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError
from pydantic import ValidationError

from core.domain.models import Plan
from core.domain.exceptions import PlannerResponseError, PlannerValidationError
from core.planner.planner_prompt import build_system_prompt

# --- One-line config point. Confirm against your NIM dashboard.
NIM_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NIM_API_KEY_ENV_VAR = "NIM_API_KEY"
REQUEST_TIMEOUT_SECONDS = 120

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """
    Lazy singleton — creates the OpenAI client once and reuses it.
    The client reads NIM_API_KEY from the environment at first call,
    not at import time, so tests can set the env var after import.
    """
    global _client
    if _client is None:
        api_key = os.environ.get(NIM_API_KEY_ENV_VAR)
        if not api_key:
            raise RuntimeError(
                f"{NIM_API_KEY_ENV_VAR} is not set. Check your .env file."
            )
        _client = OpenAI(
            base_url=NIM_BASE_URL,
            api_key=api_key,
        )
    return _client


def _strip_markdown_fences(text: str) -> str:
    """
    Best-effort cleanup for models that wrap JSON in ```json ... ```
    despite being told not to.
    """
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1) if match else text


def call(task: str, retry_context: Optional[str] = None) -> Dict[str, Any]:
    """
    Calls the Planner LLM via NIM with the given task, optionally
    including retry context from a previous failed attempt.

    Returns a dict with keys "status", "questions", "plan" -- "plan",
    if present, is a validated Plan object (not a raw dict).

    Raises:
        PlannerResponseError: network failure, HTTP error, or response
            not parseable as the expected JSON contract.
        PlannerValidationError: top-level shape is correct but the
            "plan" payload does not instantiate as a valid Plan/Step.
    """
    user_content = task
    if retry_context:
        user_content = f"{task}\n\n--- CORRECTION NEEDED ---\n{retry_context}"

    try:
        completion = _get_client().chat.completions.create(
            model=NIM_MODEL,
            messages=[
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=4096,
            timeout=REQUEST_TIMEOUT_SECONDS,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}}
        )
    except APITimeoutError as e:
        raise PlannerResponseError(
            f"NIM request timed out after {REQUEST_TIMEOUT_SECONDS}s: {e}",
            raw_response=None,
        ) from e
    except APIConnectionError as e:
        raise PlannerResponseError(
            f"NIM connection error: {e}",
            raw_response=None,
        ) from e
    except APIError as e:
        # Catches 400, 401, 429, 500, etc. -- e.status_code and e.message
        # are available on the APIError instance for debugging.
        raise PlannerResponseError(
            f"NIM API error (status {getattr(e, 'status_code', '?')}): {e}",
            raw_response=None,
        ) from e

    try:
        raw_text = completion.choices[0].message.content
    except (IndexError, AttributeError) as e:
        raise PlannerResponseError(
            f"Unexpected NIM response shape (missing choices[0].message.content): {e}",
            raw_response=str(completion),
        ) from e

    if not raw_text:
        raise PlannerResponseError(
            "NIM returned an empty response content.",
            raw_response=str(completion),
        )

    cleaned_text = _strip_markdown_fences(raw_text)

    try:
        data = json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        raise PlannerResponseError(
            f"Planner response is not valid JSON: {e}",
            raw_response=raw_text,
        ) from e

    if "status" not in data:
        raise PlannerResponseError(
            "Planner response is missing the required 'status' key.",
            raw_response=raw_text,
        )

    status = data["status"]

    if status not in ("clarification_needed", "ready"):
        raise PlannerResponseError(
            f"Planner response has an unrecognized status: '{status}'. "
            f"Expected 'clarification_needed' or 'ready'.",
            raw_response=raw_text,
        )

    if status == "clarification_needed":
        return {
            "status": "clarification_needed",
            "questions": data.get("questions", []),
            "plan": None,
        }

    # status == "ready"
    if "plan" not in data or data["plan"] is None:
        raise PlannerResponseError(
            "Planner response has status='ready' but no 'plan' payload.",
            raw_response=raw_text,
        )

    try:
        plan = Plan(**data["plan"])
    except ValidationError as e:
        raise PlannerValidationError(
            f"Planner plan payload does not match the Plan/Step schema: {e}",
            raw_response=raw_text,
            original_error=e,
        ) from e

    return {
        "status": "ready",
        "questions": [],
        "plan": plan,
    }

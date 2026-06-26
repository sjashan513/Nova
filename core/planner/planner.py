"""
Planner — real call to Kimi K2.6 via NVIDIA NIM.

This is the only module in Nova that talks to the Planner LLM over the
network. Everything downstream (the Iniciador's retry loop, the
validators) deals exclusively with Python objects -- this module is
where "text from an external API" gets turned into one of:

    - a dict matching the clarification_needed / ready contract, or
    - a PlannerError (raised), if Kimi's response can't be trusted
      enough to even build that dict.

See NOVA_PLANNER_LAYER_ADR.md §2.5 for the two-step parsing design this
implements.

Configuration note: the NIM API key is read from the NIM_API_KEY
environment variable (expected in .env, already gitignored). The model
string below is a one-line config point -- swapping models should never
require touching the call logic, per Nova's "right model, right job"
design principle.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

import requests
from pydantic import ValidationError

from core.domain.models import Plan
from core.domain.exceptions import PlannerResponseError, PlannerValidationError
from core.planner.planner_prompt import build_system_prompt

# --- One-line config point. Confirm the exact model string against your
# NIM dashboard before relying on this in production -- a wrong string
# here fails at request time (404 / model not found), not at import time.
NIM_MODEL = "moonshotai/kimi-k2.6"
NIM_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_API_KEY_ENV_VAR = "NIM_API_KEY"
REQUEST_TIMEOUT_SECONDS = 60


def _strip_markdown_fences(text: str) -> str:
    """
    Best-effort cleanup for the common case where a model wraps its JSON
    in ```json ... ``` despite being told not to. We don't rely on this
    -- response_format/json mode is the real fix when the model supports
    it -- but it costs nothing to be tolerant here before giving up.
    """
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1) if match else text


def call(task: str, retry_context: Optional[str] = None) -> Dict[str, Any]:
    """
    Calls Kimi K2.6 via NIM with the given task, optionally including
    extra context from a previous failed attempt (see
    core/planner/iniciador.py's retry loop).

    Returns a dict with keys "status", "questions", "plan" -- "plan",
    if present, is a raw dict (NOT yet a Plan object; that instantiation
    happens here too, but the validated Plan object is what's returned
    under "plan" once this function succeeds).

    Raises:
        PlannerResponseError: the response is not parseable JSON, or is
            missing the required top-level keys.
        PlannerValidationError: the top-level shape is correct but the
            "plan" payload does not instantiate as a valid Plan/Step.
    """
    api_key = os.environ.get(NIM_API_KEY_ENV_VAR)
    if not api_key:
        # Not a PlannerError -- this is a local misconfiguration, not a
        # failure of Kimi's response, so it's allowed to raise as a
        # plain RuntimeError rather than entering the domain vocabulary.
        raise RuntimeError(
            f"{NIM_API_KEY_ENV_VAR} is not set. Check your .env file."
        )

    user_content = task
    if retry_context:
        user_content = f"{task}\n\n--- CORRECTION NEEDED ---\n{retry_context}"

    payload = {
        "model": NIM_MODEL,
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
    }

    try:
        response = requests.post(
            NIM_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        raw_body = response.json()
    except requests.RequestException as e:
        # Network/HTTP failure. Treated as PlannerResponseError because,
        # from the Iniciador's point of view, it's the same situation:
        # "I don't have a usable response from Kimi." The retry loop
        # (T4) will decide whether to spend one of its attempts on this.
        raise PlannerResponseError(
            f"NIM request failed: {e}", raw_response=None
        ) from e

    try:
        raw_text = raw_body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise PlannerResponseError(
            f"Unexpected NIM response shape (missing choices[0].message.content): {e}",
            raw_response=json.dumps(raw_body),
        ) from e

    cleaned_text = _strip_markdown_fences(raw_text)

    try:
        data = json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        raise PlannerResponseError(
            f"Kimi's response is not valid JSON: {e}",
            raw_response=raw_text,
        ) from e

    if "status" not in data:
        raise PlannerResponseError(
            "Kimi's response is missing the required 'status' key.",
            raw_response=raw_text,
        )

    status = data["status"]

    if status not in ("clarification_needed", "ready"):
        raise PlannerResponseError(
            f"Kimi's response has an unrecognized status: '{status}'. "
            f"Expected 'clarification_needed' or 'ready'.",
            raw_response=raw_text,
        )

    if status == "clarification_needed":
        return {
            "status": "clarification_needed",
            "questions": data.get("questions", []),
            "plan": None,
        }

    # status == "ready" -- must have a "plan" key that instantiates
    if "plan" not in data or data["plan"] is None:
        raise PlannerResponseError(
            "Kimi's response has status='ready' but no 'plan' payload.",
            raw_response=raw_text,
        )

    try:
        plan = Plan(**data["plan"])
    except ValidationError as e:
        raise PlannerValidationError(
            f"Kimi's plan payload does not match the Plan/Step schema: {e}",
            raw_response=raw_text,
            original_error=e,
        ) from e

    return {
        "status": "ready",
        "questions": [],
        "plan": plan,
    }

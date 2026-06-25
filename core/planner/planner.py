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
from registry.tool_registry import TOOLS_BY_NAME
from registry.worker_registry import WORKERS_BY_NAME

# --- One-line config point. Confirm the exact model string against your
# NIM dashboard before relying on this in production -- a wrong string
# here fails at request time (404 / model not found), not at import time.
NIM_MODEL = "moonshotai/kimi-k2.6"
NIM_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_API_KEY_ENV_VAR = "NIM_API_KEY"
REQUEST_TIMEOUT_SECONDS = 60

_PROJECT_REGISTRY_PATH = "registry/project_registry.yaml"


def _build_registry_context() -> str:
    """
    Serializes the full Tool + Worker catalogs into a plain-text block
    for the system prompt. Kimi reads this to know what Nova can do and
    to pick exact, valid names for Step.tool_or_worker -- this is what
    makes validate_plan_against_registry's job possible in the first
    place: Kimi is never guessing a name nobody gave it.
    """
    lines: List[str] = ["AVAILABLE TOOLS (primitive, no LLM):"]
    for name, entry in TOOLS_BY_NAME.items():
        lines.append(f"  - {name}: {entry.get('description', '')}")

    lines.append("")
    lines.append("AVAILABLE WORKERS (LLM-powered, one job each):")
    for name, entry in WORKERS_BY_NAME.items():
        lines.append(f"  - {name}: {entry.get('description', '')}")

    return "\n".join(lines)


def _build_project_context() -> str:
    """Loads project_registry.yaml verbatim as text context."""
    import yaml

    with open(_PROJECT_REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    lines = ["KNOWN PROJECTS:"]
    for project_name, info in data.items():
        lines.append(
            f"  - {project_name}: path={info.get('path')}, "
            f"lang={info.get('lang')}, run={info.get('run')}, test={info.get('test')}"
        )
    return "\n".join(lines)


_SYSTEM_PROMPT_TEMPLATE = """You are Nova's Planner. You convert a natural language task into a \
structured execution plan, or ask clarifying questions if the task is \
ambiguous.

{registry_context}

{project_context}

RESPONSE CONTRACT — you must respond with ONLY valid JSON, no prose \
before or after, no markdown code fences. Your response must be exactly \
one of these two shapes:

If the task is ambiguous and you need more information before planning:
{{"status": "clarification_needed", "questions": ["...", "..."], "plan": null}}

If you can plan the task as given:
{{"status": "ready", "questions": [], "plan": {{"objective": "...", "steps": [{{"id": "...", "description": "...", "tool_or_worker": "...", "assumes": ["..."]}}]}}}}

Rules:
- "tool_or_worker" must be EXACTLY one of the names listed above. Never \
invent a name.
- Worker names always start with "worker_". Tool names never do.
- "assumes" lists the concrete assumptions this step depends on (e.g. \
which file, which branch) -- this is required for every step, even if \
the list is empty.
- Ask at most 3 questions if clarification is needed.
"""


def _build_system_prompt() -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        registry_context=_build_registry_context(),
        project_context=_build_project_context(),
    )


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
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "top_p": 0.95
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

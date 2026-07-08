"""
Worker registry loader.

Loads the `workers:` section of registry/tool_registry.yaml once at
import time and exposes the set of valid worker names. Mirrors
tool_registry.py exactly -- kept as a separate module because the ADR
treats tools (no LLM, primitive ops) and workers (LLM-powered, one
domain responsibility each) as distinct catalogs, not one undifferentiated
list.
"""

from pathlib import Path
from typing import Dict, List, Optional
import yaml

_REGISTRY_PATH = Path(__file__).parent / "tool_registry.yaml"


def _load_section(section: str) -> List[Dict]:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get(section, [])


_WORKERS = _load_section("workers")

# name -> full entry (description, module, requires_model). The
# Iniciador will use this to inject the full worker catalog into
# Kimi's planning context.
WORKERS_BY_NAME: Dict[str, Dict] = {entry["name"]: entry for entry in _WORKERS}


def list_worker_names() -> List[str]:
    """All registered worker names, e.g. 'worker_ts_fix', 'worker_jsdoc'."""
    return list(WORKERS_BY_NAME.keys())


def worker_exists(name: str) -> bool:
    return name in WORKERS_BY_NAME


def worker_requires_model(name: str) -> bool:
    """
    Fase 3 addition. True if the named worker is LLM-powered and
    therefore needs Step.model set when planned (registry entry has
    requires_model: true) -- see core/planner/validators.py::
    validate_worker_steps_have_model.

    Returns False for both: workers explicitly marked
    requires_model: false (e.g. worker_ts_check -- "No LLM"), and
    unknown worker names. The latter is deliberately permissive here
    -- flagging an unknown worker name is validate_plan_against_
    registry's job (WorkerNotFoundError), not this function's;
    returning False just means validate_worker_steps_have_model stays
    silent on it and lets that other check report the real problem,
    instead of producing a second, more confusing error for the same
    root cause.
    """
    entry = WORKERS_BY_NAME.get(name)
    if entry is None:
        return False
    return bool(entry.get("requires_model", False))


def get_worker_default_model(name: str) -> Optional[str]:
    """
    Returns the default_model string for a worker that requires one,
    or None if the worker doesn't require a model or doesn't exist.
    The Director uses this to inject a model into worker steps where
    step.model was not set by the Planner -- the Planner no longer
    needs to know or care about model strings.
    """
    entry = WORKERS_BY_NAME.get(name)
    if entry is None:
        return None
    return entry.get("default_model", None)

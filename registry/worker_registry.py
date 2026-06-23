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
from typing import Dict, List
import yaml

_REGISTRY_PATH = Path(__file__).parent / "tool_registry.yaml"


def _load_section(section: str) -> List[Dict]:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get(section, [])


_WORKERS = _load_section("workers")

# name -> full entry (description, module). The Iniciador will use this
# to inject the full worker catalog into Kimi's planning context.
WORKERS_BY_NAME: Dict[str, Dict] = {entry["name"]: entry for entry in _WORKERS}


def list_worker_names() -> List[str]:
    """All registered worker names, e.g. 'worker_ts_fix', 'worker_jsdoc'."""
    return list(WORKERS_BY_NAME.keys())


def worker_exists(name: str) -> bool:
    return name in WORKERS_BY_NAME

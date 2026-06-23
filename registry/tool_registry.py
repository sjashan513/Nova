"""
Tool registry loader.

Loads the `tools:` section of registry/tool_registry.yaml once at import
time and exposes the set of valid primitive tool names. This module does
nothing else -- no dispatch, no execution. That belongs to Fase 1+.
"""

from pathlib import Path
from typing import Dict, List
import yaml

_REGISTRY_PATH = Path(__file__).parent / "tool_registry.yaml"


def _load_section(section: str) -> List[Dict]:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get(section, [])


_TOOLS = _load_section("tools")

# name -> full entry (description, module), for future use by the
# Iniciador when injecting registry context into the Planner prompt.
TOOLS_BY_NAME: Dict[str, Dict] = {entry["name"]: entry for entry in _TOOLS}


def list_tool_names() -> List[str]:
    """All registered primitive tool names, e.g. 'filesystem', 'terminal'."""
    return list(TOOLS_BY_NAME.keys())


def tool_exists(name: str) -> bool:
    return name in TOOLS_BY_NAME

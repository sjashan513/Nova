"""
Project registry loader.

Loads registry/project_registry.yaml once at import time. Distinct
catalog from tool/worker registries: this one describes the actual
codebases Nova operates on (Pulse, Nova itself) -- path, language, run
command, test command -- not the tools/workers available to act on
them. Mirrors tool_registry.py / worker_registry.py's loading pattern
for consistency, even though the YAML shape here is a flat
name -> metadata dict rather than a list of entries (no "name" field
needed inside each entry -- the YAML key already is the name).
"""

from pathlib import Path
from typing import Dict, List
import yaml

_REGISTRY_PATH = Path(__file__).parent / "project_registry.yaml"


def _load_registry() -> Dict[str, Dict]:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Expand "~" once, here, so every consumer (a Worker that needs a
    # real filesystem path to run a subprocess against) always gets a
    # ready-to-use absolute path -- same reasoning as
    # parse_json_response existing as one shared function instead of
    # five workers each remembering to call os.path.expanduser()
    # themselves.
    for entry in data.values():
        if isinstance(entry, dict) and "path" in entry:
            entry["path"] = str(Path(entry["path"]).expanduser())

    return data


PROJECTS_BY_NAME: Dict[str, Dict] = _load_registry()


def list_project_names() -> List[str]:
    """All registered project names, e.g. 'Pulse', 'Nova'."""
    return list(PROJECTS_BY_NAME.keys())


def project_exists(name: str) -> bool:
    return name in PROJECTS_BY_NAME


def get_project(name: str) -> Dict:
    """
    Returns the full registry entry for `name` (path, lang, run, test
    -- already expanded). Raises KeyError if `name` doesn't exist --
    deliberately loud rather than returning None: a Worker calling
    this should already have checked project_exists() (or rely on
    validate_step_projects_exist having caught it upstream) -- a
    KeyError here means that expectation was violated somewhere, which
    is worth failing loudly on rather than silently returning a
    half-built dict.
    """
    return PROJECTS_BY_NAME[name]

"""
core/init/registry_loader.py — carga de registros YAML al arrancar Nova.

Responsabilidad única: leer tool_registry.yaml y project_registry.yaml
del disco una sola vez al arrancar y devolverlos como dicts.

Los registros son inmutables en runtime — no hay razón para recargarlos
por plan. cli.py los recibe y los pasa al Director en cada ejecución.
"""

from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

_TOOL_REGISTRY_PATH = Path("registry/tool_registry.yaml")
_PROJECT_REGISTRY_PATH = Path("registry/project_registry.yaml")


def load_registries() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Carga y devuelve (registry, projects).

    Returns:
        registry: contenido completo de tool_registry.yaml
        projects: contenido completo de project_registry.yaml (vacío si no existe)
    """
    with _TOOL_REGISTRY_PATH.open(encoding="utf-8") as f:
        registry = yaml.safe_load(f)

    with _PROJECT_REGISTRY_PATH.open(encoding="utf-8") as f:
        projects = yaml.safe_load(f) or {}

    return registry, projects

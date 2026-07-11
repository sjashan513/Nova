"""
memory/tool/memory_tool.py — tools OpenAI-compatible invocables por Qwen.

Expone dos tools:
  memory_tool — busca contexto en la memoria de Nova
  nova_plan   — señal para que cli.py arranque el Planner + Director

El MemoryRouter se instancia como singleton interno al importar.
cli.py no gestiona su ciclo de vida — lo desconoce completamente.

Uso en cli.py:
    from memory.tool.memory_tool import TOOLS, execute_memory

    # pasar TOOLS a call_groq
    # en _handle_tool_calls detectar por tool_name
"""

import logging
import os
from typing import List

from memory.router.memory_router import MemoryRouter
from memory.zep.zep_client import ZepClient
from memory.codebase_mcp.query_engine import QueryEngine

logger = logging.getLogger(__name__)

_zep = ZepClient(api_key=os.environ["ZEP_API_KEY"])
_query_engine = QueryEngine()
_router = MemoryRouter(zep=_zep, query_engine=_query_engine)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_MEMORY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory_tool",
        "description": (
            "Busca contexto relevante en la memoria de Nova. "
            "Usa esta tool cuando el usuario pregunte por algo que ocurrió "
            "en sesiones anteriores: errores pasados, decisiones tomadas, "
            "workers o ficheros existentes, historial de tareas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "archipelago": {
                    "type": "string",
                    "enum": ["dev", "personal"],
                    "description": (
                        "Dominio al que pertenece la query. "
                        "'dev' para código, proyectos y decisiones técnicas. "
                        "'personal' para conversación general y preferencias."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Pregunta en lenguaje natural sobre el contexto. "
                        "Sé específico: incluye nombres de ficheros, workers "
                        "o proyectos cuando los conozcas."
                    ),
                },
            },
            "required": ["archipelago", "query"],
        },
    },
}

_NOVA_PLAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "nova_plan",
        "description": (
            "Lanza una tarea ejecutable en Nova: planifica con el Planner "
            "y ejecuta con el Director y sus workers. "
            "Usa esta tool cuando el usuario pida hacer algo concreto sobre "
            "un proyecto: arreglar errores, añadir JSDoc, generar tests, "
            "hacer un commit, refactorizar código, etc. "
            "Antes de emitir nova_plan, incluye siempre una frase corta en "
            "content confirmando que has entendido la tarea y que arrancas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Descripción precisa de la tarea a ejecutar. "
                        "Si consultaste memory_tool antes, incorpora el contexto "
                        "relevante que encontraste para que el Planner no tenga "
                        "que buscarlo de nuevo."
                    ),
                },
            },
            "required": ["task"],
        },
    },
}

TOOLS = [_MEMORY_TOOL_SCHEMA, _NOVA_PLAN_SCHEMA]

# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def _format_results(results: List[dict]) -> str:
    if not results:
        return "No se encontró información relevante."

    lines = []
    for r in results:
        source = r.get("source", "unknown")

        if source == "zep":
            content = r.get("content", "").strip()
            if content:
                lines.append(content)

        elif source == "codebase":
            file = r.get("file", "")
            line = r.get("line", "")
            content = r.get("content", "").strip()
            if file and content:
                lines.append(f"{file}:{line} — {content}")

        else:
            content = r.get("content", "").strip()
            if content:
                lines.append(content)

    return "\n".join(lines) if lines else "No se encontró información relevante."


# ---------------------------------------------------------------------------
# Entry points públicos
# ---------------------------------------------------------------------------

def execute_memory(archipelago: str, query: str) -> str:
    """
    Ejecuta una query de memoria y devuelve texto plano para Qwen.
    Nunca lanza excepción — degrada a mensaje de error legible.
    """
    try:
        result = _router.query(archipelago=archipelago, query=query)

        logger.debug(
            "[memory_tool] archipelago='%s' island='%s' type='%s' n_results=%d",
            archipelago,
            result.get("island"),
            result.get("type"),
            len(result.get("results", [])),
        )

        return _format_results(result.get("results", []))

    except Exception as e:
        logger.error("[memory_tool] query failed: %s", e)
        return f"Error al consultar la memoria: {e}"

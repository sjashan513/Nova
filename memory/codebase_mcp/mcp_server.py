"""
MCP Server — expone QueryEngine como MCP tool invocable por Qwen.

En el MVP este servidor es un wrapper síncrono ligero sobre QueryEngine.
No levanta un proceso HTTP separado — el MemoryRouter lo llama
directamente como librería Python.

La interfaz imita el shape MCP para facilitar la migración futura a un
MCP server real (stdio o HTTP) sin cambiar el contrato del router.

Tool expuesto:
  codebase_search(island: str, query: str) → List[dict]
"""

import logging
from typing import List, Dict, Any

from memory.codebase_mcp.query_engine import QueryEngine

logger = logging.getLogger(__name__)


class CodebaseMCPServer:
    """
    Wrapper MCP-compatible sobre QueryEngine.

    En el MVP actúa como librería síncrona. En producción futura puede
    exponerse como MCP server stdio o HTTP sin cambiar la interfaz.
    """

    def __init__(self) -> None:
        self._engine = QueryEngine()

    def codebase_search(self, island: str, query: str) -> List[Dict[str, Any]]:
        """
        MCP tool: structural search over the codebase index.

        Args:
          island: island name as defined in archipelagos.yaml ("Nova", "Pulse")
          query:  natural language query about code structure

        Returns:
          List of result dicts — shape depends on query type
          (symbols, usages, or registry entries).
        """
        logger.debug(
            "[CodebaseMCP] codebase_search island='%s' query='%s'", island, query
        )
        results = self._engine.search(island_name=island, query=query)
        logger.debug("[CodebaseMCP] returned %d result(s)", len(results))
        return results

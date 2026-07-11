"""
MemoryRouter — entrada única al sistema de memoria en el query path.

Recibe {archipelago, query}, identifica la isla, clasifica el tipo
de query, y despacha al motor correcto:

  episodic   → Zep (historial, decisiones, errores pasados)
  structural → codebase-mcp (estado actual del código)
  ambiguous  → ambos en paralelo, resultados combinados

session_id es siempre date.today().isoformat() — generado internamente.
El caller no necesita conocer o gestionar el session_id.

El router es stateless — puede ser instanciado una vez y reutilizado
durante toda la sesión sin efectos secundarios.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from memory.router.island_resolver import resolve
from memory.router.query_classifier import classify
from memory.zep.zep_client import ZepClient
from memory.codebase_mcp.query_engine import QueryEngine

logger = logging.getLogger(__name__)

_ARCHIPELAGOS_PATH = Path(__file__).parent / "archipelagos.yaml"


class MemoryRouter:
    def __init__(self, zep: ZepClient, query_engine: QueryEngine) -> None:
        self._zep = zep
        self._query_engine = query_engine
        self._archipelagos = self._load_archipelagos()

    def _load_archipelagos(self) -> Dict[str, Any]:
        with _ARCHIPELAGOS_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("archipelagos", {})

    def query(self, archipelago: str, query: str) -> Dict[str, Any]:
        """
        Main entry point for all memory queries.

        Returns:
          {
            "island":   str | None,
            "type":     "episodic" | "structural" | "ambiguous",
            "results":  List[dict]
          }

        Results shape varies by motor:
          Zep:          [{"message": {...}, "score": float}, ...]
          codebase-mcp: [{"file": str, "line": int, "content": str}, ...]
          ambiguous:    Zep results first, then codebase-mcp results
        """
        arch = self._archipelagos.get(archipelago)
        if arch is None:
            logger.warning(
                "[MemoryRouter] unknown archipelago '%s'", archipelago)
            return {"island": None, "type": "ambiguous", "results": []}

        islands = arch.get("islands", [])
        island_name = resolve(query, islands)
        query_type = classify(query)
        session_id = date.today().isoformat()

        logger.debug(
            "[MemoryRouter] archipelago='%s' island='%s' type='%s' query='%s'",
            archipelago, island_name, query_type, query,
        )

        if query_type == "episodic":
            results = self._query_zep(session_id, query)
        elif query_type == "structural":
            results = self._query_codebase(island_name, query)
        else:
            # ambiguous — both engines in parallel
            results = self._query_both(session_id, island_name, query)

        return {
            "island": island_name,
            "type": query_type,
            "results": results,
        }

    def _query_zep(self, session_id: str, query: str) -> List[dict]:
        return self._zep.search(session_id=session_id, query=query, limit=5)

    def _query_codebase(self, island_name: Optional[str], query: str) -> List[dict]:
        if island_name is None:
            return []
        return self._query_engine.search(island_name=island_name, query=query)

    def _query_both(
        self, session_id: str, island_name: Optional[str], query: str
    ) -> List[dict]:
        """
        Launches both engines in parallel via ThreadPoolExecutor.
        Zep results come first in the combined list, then codebase-mcp.
        Each engine's failure is isolated — one failing does not abort the other.
        """
        zep_results: List[dict] = []
        codebase_results: List[dict] = []

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(self._query_zep, session_id, query): "zep",
                executor.submit(self._query_codebase, island_name, query): "codebase",
            }
            for future in as_completed(futures):
                engine = futures[future]
                try:
                    result = future.result()
                    if engine == "zep":
                        zep_results = result
                    else:
                        codebase_results = result
                except Exception as e:
                    logger.error(
                        "[MemoryRouter] %s engine failed during ambiguous query: %s",
                        engine, e,
                    )

        return zep_results + codebase_results

"""
QueryEngine — responde queries estructurales sobre el índice codebase-mcp.

Lee de index.db (generado por indexer.py). No modifica el índice.
Sin LLM — todas las queries son SQL sobre el índice de tree-sitter.

Tres tipos de queries:
  1. symbols  — qué funciones/clases/tipos exporta un fichero o isla
  2. usages   — dónde se usa un símbolo concreto (por nombre)
  3. registry — entradas del tool_registry o project_registry

La clasificación de qué tipo de query SQL lanzar es determinística
por keywords en el texto de la query.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

_INDEX_DB_PATH = Path(__file__).parent / "index.db"


def _get_conn() -> sqlite3.Connection:
    if not _INDEX_DB_PATH.exists():
        raise FileNotFoundError(
            f"index.db not found at {_INDEX_DB_PATH}. "
            "Run indexer.index_island() before querying."
        )
    conn = sqlite3.connect(str(_INDEX_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


class QueryEngine:
    def search(self, island_name: str, query: str) -> List[Dict[str, Any]]:
        """
        Routes the query to the appropriate SQL query based on intent keywords.

        Returns a list of result dicts. Shape varies by query type:
          symbols:  [{"name": str, "kind": str, "file": str, "line": int}, ...]
          usages:   [{"name": str, "file": str, "line": int}, ...]
          registry: [{"entry_name": str, "registry": str, "data": dict}, ...]
        """
        q = query.lower()

        try:
            conn = _get_conn()
        except FileNotFoundError as e:
            logger.error("[QueryEngine] %s", e)
            return []

        try:
            if self._is_registry_query(q):
                return self._query_registry(conn, island_name, query)
            elif self._is_usage_query(q):
                return self._query_usages(conn, island_name, query)
            else:
                return self._query_symbols(conn, island_name, query)
        except Exception as e:
            logger.error("[QueryEngine] query failed for island='%s' query='%s': %s",
                         island_name, query, e)
            return []
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Intent detection
    # ------------------------------------------------------------------

    def _is_registry_query(self, q: str) -> bool:
        return bool(re.search(
            r"\b(workers?|tools?|registry|schema|registered|registrado|existen|hay|disponibles?)\b", q
        ))

    def _is_usage_query(self, q: str) -> bool:
        return bool(re.search(
            r"\b(dónde|donde|usa|usan|import|importa|aparece|referencia)\b", q
        ))

    # ------------------------------------------------------------------
    # SQL queries
    # ------------------------------------------------------------------

    def _query_symbols(self, conn: sqlite3.Connection, island: str, query: str) -> List[dict]:
        """
        Returns exported symbols for the island.
        Optionally filtered by a keyword extracted from the query.
        """
        # Try to extract a specific symbol name from the query
        # e.g. "qué hace WorkerTsFix" → filter by name LIKE "%WorkerTsFix%"
        keyword = self._extract_symbol_keyword(query)

        if keyword:
            rows = conn.execute(
                """SELECT name, kind, file_path, line FROM symbols
                   WHERE island = ? AND name LIKE ? AND exported = 1
                   ORDER BY file_path, line""",
                (island, f"%{keyword}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT name, kind, file_path, line FROM symbols
                   WHERE island = ? AND exported = 1
                   ORDER BY file_path, line
                   LIMIT 50""",
                (island,),
            ).fetchall()

        return [
            {"name": r["name"], "kind": r["kind"],
                "file": r["file_path"], "line": r["line"]}
            for r in rows
        ]

    def _query_usages(self, conn: sqlite3.Connection, island: str, query: str) -> List[dict]:
        """
        Returns files that import or reference a given symbol name.
        """
        keyword = self._extract_symbol_keyword(query)
        if not keyword:
            return []

        # Search in symbols (definitions) and imports (usages)
        symbol_rows = conn.execute(
            """SELECT name, file_path, line FROM symbols
               WHERE island = ? AND name LIKE ?
               ORDER BY file_path, line""",
            (island, f"%{keyword}%"),
        ).fetchall()

        import_rows = conn.execute(
            """SELECT imported_from, file_path, names FROM imports
               WHERE island = ? AND names LIKE ?
               ORDER BY file_path""",
            (island, f"%{keyword}%"),
        ).fetchall()

        results = [
            {"name": r["name"], "file": r["file_path"],
                "line": r["line"], "kind": "definition"}
            for r in symbol_rows
        ]
        results += [
            {
                "name": keyword,
                "file": r["file_path"],
                "line": 0,
                "kind": "import",
                "imported_from": r["imported_from"],
            }
            for r in import_rows
        ]
        return results

    def _query_registry(self, conn: sqlite3.Connection, island: str, query: str) -> List[dict]:
        """
        Returns registry entries (workers, tools, projects) matching the query.
        """
        keyword = self._extract_symbol_keyword(query)

        if keyword:
            rows = conn.execute(
                """SELECT registry, entry_name, entry_data FROM registry_entries
                   WHERE island = ? AND entry_name LIKE ?
                   ORDER BY registry, entry_name""",
                (island, f"%{keyword}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT registry, entry_name, entry_data FROM registry_entries
                   WHERE island = ?
                   ORDER BY registry, entry_name""",
                (island,),
            ).fetchall()

        return [
            {
                "registry": r["registry"],
                "entry_name": r["entry_name"],
                "data": json.loads(r["entry_data"]),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_symbol_keyword(self, query: str) -> str:
        """
        Tries to extract a meaningful symbol name from the query text.
        Removes common stop words and returns the most likely symbol name.
        """
        stop_words = {
            "qué", "que", "cuáles", "cuales", "lista", "dame", "muestra",
            "hay", "existe", "existen", "workers", "worker", "funciones",
            "función", "clases", "clase", "en", "de", "del", "la", "el",
            "los", "las", "nova", "pulse", "dónde", "donde", "usa", "usan",
            "se", "importa", "están", "están", "definidos", "actualmente",
            "hoy", "ahora", "todos", "todas",
        }
        words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query)
        candidates = [w for w in words if w.lower(
        ) not in stop_words and len(w) > 2]

        # Prefer camelCase or snake_case tokens (more likely to be symbol names)
        for c in candidates:
            if re.search(r"[A-Z]|_", c):
                return c

        return candidates[0] if candidates else ""

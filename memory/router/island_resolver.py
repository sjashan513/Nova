"""
IslandResolver — identifica qué isla (repositorio) corresponde a una query.

Estrategia en dos pasos:
  1. Regex sobre keywords de cada isla — 0ms, cubre ~80% de casos.
     Primer match gana — el orden en archipelagos.yaml importa.
  2. Embedder CPU (all-MiniLM-L6-v2) como fallback — ~10ms,
     cubre queries ambiguas sin keywords explícitas.

Devuelve el nombre de la isla (str) o None si el archipiélago no
tiene islas definidas.
"""

import logging
import re
from typing import List, Optional

from memory.embedder.embedder import rank_islands

logger = logging.getLogger(__name__)


def resolve(query: str, islands: List[dict]) -> Optional[str]:
    """
    Identifies the most relevant island for the given query.

    islands: list of dicts with keys {name, keywords, path}
    Returns the island name, or None if islands is empty.
    """
    if not islands:
        return None

    query_lower = query.lower()

    # Step 1 — regex keyword matching (fast path)
    for island in islands:
        for keyword in island["keywords"]:
            if re.search(rf"\b{re.escape(keyword)}\b", query_lower):
                logger.debug(
                    "[IslandResolver] regex match: query='%s' → island='%s' (keyword='%s')",
                    query,
                    island["name"],
                    keyword,
                )
                return island["name"]

    # Step 2 — embedder fallback (slow path, only on regex miss)
    logger.debug(
        "[IslandResolver] no regex match for query='%s', falling back to embedder", query
    )
    return rank_islands(query, islands)

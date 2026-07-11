"""
Embedder — island identifier via semantic similarity.

Uses all-MiniLM-L6-v2 (~80MB, CPU-only) to rank islands by cosine
similarity against the query. Only loaded on first use (lazy singleton)
and only called when regex matching in island_resolver fails to match.

Never called by the Director or the Planner — only by island_resolver
as a fallback when keyword matching returns no result.
"""

import logging
from typing import List

import numpy as np

logger = logging.getLogger(__name__)

_model = None  # lazy — loaded on first call, lives in RAM until process exits


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("[Embedder] loading all-MiniLM-L6-v2 on CPU...")
        _model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
        logger.info("[Embedder] model loaded")
    return _model


def rank_islands(query: str, islands: List[dict]) -> str:
    """
    Ranks islands by cosine similarity between the query embedding
    and each island's keyword concatenation embedding.

    Always returns the name of the best-matching island — no threshold.
    The caller (island_resolver) decides whether to trust the result.

    islands: list of dicts with keys {name, keywords, path}
    """
    model = _get_model()

    island_texts = [" ".join(island["keywords"]) for island in islands]
    all_texts = [query] + island_texts

    embeddings = model.encode(
        all_texts, convert_to_numpy=True, normalize_embeddings=True)

    query_emb = embeddings[0]           # shape (384,)
    island_embs = embeddings[1:]        # shape (N, 384)

    # Cosine similarity — embeddings are L2-normalised so dot product = cosine
    scores = island_embs @ query_emb    # shape (N,)

    best_idx = int(np.argmax(scores))
    best_island = islands[best_idx]["name"]

    logger.debug(
        "[Embedder] query='%s' scores=%s best='%s'",
        query,
        {islands[i]["name"]: round(float(scores[i]), 3)
         for i in range(len(islands))},
        best_island,
    )

    return best_island

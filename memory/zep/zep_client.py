"""
ZepClient — wrapper sobre zep-cloud SDK v3.

API real de Zep Cloud v3 (zep-cloud==3.24.0):
  Modelo mental:
    user.add(user_id)               → upsert usuario (una vez)
    thread.create(thread_id, user_id) → crea thread bajo el usuario
    thread.add_messages(thread_id, messages) → escribe mensajes
    graph.search(user_id, query)    → búsqueda semántica en el grafo del usuario

  En Nova:
    user_id  = "jashan" (fijo — un solo usuario)
    thread_id = session_id = fecha ISO ("2026-07-11")
    Un thread = un día de trabajo

  Flujo write:
    _ensure_user() → _ensure_thread(session_id) → add_memory()

  Flujo read:
    search(session_id, query) → graph.search(user_id, query, scope="episodes")
"""

import logging
import os
from typing import List, Optional

from zep_cloud.client import Zep
from zep_cloud.types import Message

logger = logging.getLogger(__name__)

_USER_ID = "jashan"  # Nova es single-user — user_id fijo


class ZepClient:
    def __init__(self, api_key: str) -> None:
        self._client = Zep(api_key=api_key)
        self._user_ensured = False    # upsert user once per process lifetime
        self._threads: set = set()    # threads created in this process

    def _ensure_user(self) -> None:
        """
        Upserts the Nova user in Zep Cloud. Called once per process.
        Zep returns 409 if user already exists — we ignore it.
        """
        if self._user_ensured:
            return
        try:
            self._client.user.add(user_id=_USER_ID)
            logger.debug("[ZepClient] user '%s' ensured", _USER_ID)
        except Exception as e:
            if "409" in str(e) or "already exists" in str(e).lower():
                pass  # user already exists — fine
            else:
                logger.warning("[ZepClient] user.add warning: %s", e)
        self._user_ensured = True

    def _ensure_thread(self, thread_id: str) -> None:
        """
        Creates the Zep thread (= session = day) if not already created
        in this process. Zep returns 409 if thread exists — we ignore it.
        """
        if thread_id in self._threads:
            return
        try:
            self._client.thread.create(thread_id=thread_id, user_id=_USER_ID)
            logger.debug("[ZepClient] thread '%s' created", thread_id)
        except Exception as e:
            if "409" in str(e) or "already exists" in str(e).lower():
                pass  # thread already exists — fine
            else:
                logger.warning(
                    "[ZepClient] thread.create warning for %s: %s", thread_id, e)
        self._threads.add(thread_id)

    def add_memory(self, session_id: str, messages: List[dict]) -> None:
        """
        Writes memory messages to the Zep thread for this session.

        messages shape (from Extractor):
          [{"role": "system", "content": "...", "metadata": {...}}]

        Zep Cloud requires user + thread to exist before adding messages.
        Both are upserted automatically here.
        """
        try:
            self._ensure_user()
            self._ensure_thread(session_id)

            zep_messages = [
                Message(
                    role=m["role"],
                    content=m["content"],
                )
                for m in messages
            ]
            self._client.thread.add_messages(session_id, messages=zep_messages)
            logger.debug("[ZepClient] added %d message(s) to thread '%s'", len(
                zep_messages), session_id)

        except Exception as e:
            logger.error(
                "[ZepClient] add_memory failed for session %s: %s", session_id, e
            )
            raise  # re-raise so Bibliotecario leaves the event unprocessed

    def search(self, session_id: str, query: str, limit: int = 5) -> List[dict]:
        """
        Semantic search over the Zep user graph, scoped to episodes.

        Returns:
          [{"content": str, "score": float, "metadata": dict}, ...]

        Returns [] on failure — query path degrades gracefully.
        """
        try:
            self._ensure_user()

            results = self._client.graph.search(
                query=query,
                user_id=_USER_ID,
                scope="episodes",
                limit=limit,
            )

            episodes = results.episodes or []
            return [
                {
                    "content": ep.content if hasattr(ep, "content") else str(ep),
                    "score": getattr(ep, "score", 0.0),
                    "metadata": {},
                }
                for ep in episodes
            ]

        except Exception as e:
            logger.error(
                "[ZepClient] search failed query='%s': %s", query, e
            )
            return []

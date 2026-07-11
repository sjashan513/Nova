"""
WALManager — única interfaz pública al subsistema WAL.

El Director y el Bibliotecario importan solo esto. WALWriter y WALReader
son detalles de implementación internos — nadie fuera de memory/wal/
los instancia directamente.

El lock vive en _wal_lock.py y es adquirido dentro de WALWriter y
WALReader. WALManager no necesita saber que existe — es transparente.
"""

from typing import List

from memory.wal.wal_writer import WALWriter
from memory.wal.wal_reader import WALReader


class WALManager:
    def __init__(self) -> None:
        self._writer = WALWriter()
        self._reader = WALReader()

    def append(self, event: dict) -> str:
        """
        Appends a new event to the WAL. Returns the generated event_id.
        Called by the Director after a memory_critical step completes.
        """
        return self._writer.append(event)

    def unprocessed(self) -> List[dict]:
        """
        Returns all unprocessed events as a materialised list.
        Safe to call from the Bibliotecario background thread — the lock
        is released before this returns, so the Director can append freely
        while the Bibliotecario iterates the returned list.
        """
        return self._reader.unprocessed()

    def mark_processed(self, event_id: str) -> None:
        """
        Marks a single event as processed. Called by the Bibliotecario
        after SQLite + Zep writes both succeed for that event.
        """
        self._reader.mark_processed(event_id)

    def compact(self) -> int:
        """
        Removes all processed events from the WAL file, keeping only
        unprocessed ones. Returns the number of events removed.

        TODO: call from bibliotecario.start() on Nova startup, and
        from the poll loop when processed event count exceeds 500.
        Deferred — stub only for now.
        """
        # TODO: implement compaction
        return 0

"""
WALReader — reads and marks events in the WAL.

Private to memory/wal/. Use WALManager, never import this directly.
Both unprocessed() and mark_processed() acquire WAL_LOCK:
  - unprocessed() materialises the full list inside the lock so the
    lock is released before the caller iterates (no lock held during
    Bibliotecario processing time).
  - mark_processed() rewrites the entire file; it must not race with
    an append() from the Director.
"""

import json
from pathlib import Path
from typing import List

from memory.wal._wal_lock import WAL_LOCK

WAL_PATH = Path(__file__).parent / "nova_wal.jsonl"


class WALReader:
    def unprocessed(self) -> List[dict]:
        """
        Returns all unprocessed events as a materialised list.
        The lock is acquired only for the file read, then released
        before the caller iterates — the Director can append freely
        while the Bibliotecario processes the returned list.
        """
        with WAL_LOCK:
            if not WAL_PATH.exists():
                return []
            events = []
            with WAL_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if not entry.get("processed", False):
                        events.append(entry)
            return events

    def mark_processed(self, event_id: str) -> None:
        with WAL_LOCK:
            if not WAL_PATH.exists():
                return
            lines = WAL_PATH.read_text(encoding="utf-8").splitlines()
            updated = []
            for line in lines:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry["event_id"] == event_id:
                    entry["processed"] = True
                updated.append(json.dumps(entry))
            WAL_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")

"""
WALWriter — append-only log writer.

Private to memory/wal/. Use WALManager, never import this directly.
All file operations are held under WAL_LOCK to prevent races with
WALReader.mark_processed(), which rewrites the entire file.
"""

import json
import time
import uuid
from pathlib import Path

from memory.wal._wal_lock import WAL_LOCK

WAL_PATH = Path(__file__).parent / "nova_wal.jsonl"


class WALWriter:
    def __init__(self) -> None:
        WAL_PATH.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: dict) -> str:
        event_id = str(uuid.uuid4())
        entry = {
            "event_id":  event_id,
            "processed": False,
            "ts":        int(time.time()),
            **event,
        }
        with WAL_LOCK:
            with WAL_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        return event_id

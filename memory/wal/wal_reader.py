import json
from pathlib import Path
from typing import Generator

WAL_PATH = Path(__file__).parent / "nova_wal.jsonl"


class WALReader:
    def unprocessed(self) -> Generator[dict, None, None]:
        if not WAL_PATH.exists():
            return
        with WAL_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if not entry.get("processed", False):
                    yield entry

    def mark_processed(self, event_id: str) -> None:
        if not WAL_PATH.exists():
            return
        lines = WAL_PATH.read_text(encoding="utf-8").splitlines()
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry["event_id"] == event_id:
                entry["processed"] = True
            updated.append(json.dumps(entry))
        WAL_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")

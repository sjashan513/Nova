import json
import sqlite3
import time
import uuid
from typing import Optional

from memory.sqlite.schema import init_db


class SQLiteWriter:
    def __init__(self) -> None:
        self._conn: sqlite3.Connection = init_db()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def open_session(self, domain: str = "dev") -> str:
        session_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO sessions (id, domain, ts_start) VALUES (?, ?, ?)",
            (session_id, domain, int(time.time())),
        )
        self._conn.commit()
        return session_id

    def close_session(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET ts_end = ? WHERE id = ?",
            (int(time.time()), session_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    def write_turn(
        self,
        session_id: str,
        input_text: str,
        output_text: Optional[str] = None,
        turn_id_override: Optional[str] = None,
    ) -> str:
        turn_id = turn_id_override or str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO turns (id, session_id, input_text, output_text, ts) VALUES (?, ?, ?, ?, ?)",
            (turn_id, session_id, input_text, output_text, int(time.time())),
        )
        self._conn.commit()
        return turn_id

    def update_turn_output(self, turn_id: str, output_text: str) -> None:
        self._conn.execute(
            "UPDATE turns SET output_text = ? WHERE id = ?",
            (output_text, turn_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------

    def open_plan(self, turn_id: str, plan_id: str, task: str) -> None:
        self._conn.execute(
            """
            INSERT INTO plans (id, turn_id, task, status, worker_sequence, ts_start)
            VALUES (?, ?, ?, 'PENDING', '[]', ?)
            """,
            (plan_id, turn_id, task, int(time.time())),
        )
        self._conn.commit()

    def update_plan_status(
        self,
        plan_id: str,
        status: str,
        worker_sequence: Optional[list] = None,
    ) -> None:
        if worker_sequence is not None:
            self._conn.execute(
                "UPDATE plans SET status = ?, worker_sequence = ? WHERE id = ?",
                (status, json.dumps(worker_sequence), plan_id),
            )
        else:
            self._conn.execute(
                "UPDATE plans SET status = ? WHERE id = ?",
                (status, plan_id),
            )
        self._conn.commit()

    def close_plan(self, plan_id: str, status: str, worker_sequence: list) -> None:
        self._conn.execute(
            "UPDATE plans SET status = ?, worker_sequence = ?, ts_end = ? WHERE id = ?",
            (status, json.dumps(worker_sequence), int(time.time()), plan_id),
        )
        self._conn.commit()

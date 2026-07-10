"""
Bibliotecario — WAL consumer.

Reads unprocessed memory_critical events from the WAL and writes them
into SQLite (sessions → turns → plans). Called once at CLI startup,
before the main loop, after _recover_wal().

One plan may produce multiple memory_critical events (one per
memory_critical worker). The Bibliotecario groups them by plan_id
so a single plan row is written per plan, with all workers in
worker_sequence order.
"""

from collections import defaultdict
from typing import List, Dict

from memory.sqlite.writer import SQLiteWriter
from memory.wal.wal_reader import WALReader


def run() -> int:
    """
    Processes all pending WAL events. Returns the number of events processed.
    """
    reader = WALReader()
    writer = SQLiteWriter()

    pending = list(reader.unprocessed())
    if not pending:
        return 0

    # Group by plan_id — one plan may have multiple memory_critical events
    plans: Dict[str, List[dict]] = defaultdict(list)
    for event in pending:
        plans[event["plan_id"]].append(event)

    for plan_id, events in plans.items():
        # All events for the same plan share session_id, turn_id, task
        first = events[0]
        session_id = first["session_id"]
        turn_id = first["turn_id"]
        task = first["task"]

        # Upsert session — the day may already exist from a prior plan
        existing = writer._conn.execute(
            "SELECT id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not existing:
            writer._conn.execute(
                "INSERT INTO sessions (id, domain, ts_start) VALUES (?, 'dev', ?)",
                (session_id, first["ts"]),
            )
            writer._conn.commit()

        # Upsert turn — turn_id is unique per DirectorInstance
        existing_turn = writer._conn.execute(
            "SELECT id FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()
        if not existing_turn:
            writer.write_turn(
                session_id=session_id,
                input_text=task,
                turn_id_override=turn_id,
            )

        # Build worker_sequence in event arrival order (WAL is append-only)
        worker_sequence = [e["worker"] for e in events]

        # Upsert plan
        existing_plan = writer._conn.execute(
            "SELECT id FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not existing_plan:
            writer.open_plan(turn_id=turn_id, plan_id=plan_id, task=task)

        writer.close_plan(
            plan_id=plan_id,
            status="DONE",
            worker_sequence=worker_sequence,
        )

        # Mark all events for this plan as processed
        for event in events:
            reader.mark_processed(event["event_id"])

    return len(pending)

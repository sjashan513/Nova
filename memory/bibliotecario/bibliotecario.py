"""
Bibliotecario — WAL consumer con polling en background.

Dos responsabilidades:
  1. Pasada inicial síncrona al arrancar Nova — procesa eventos
     pendientes de sesiones anteriores antes de que el CLI arranque.
  2. Polling en background cada 60s — procesa eventos emitidos
     por el Director durante la sesión en curso.

Por cada grupo de eventos (agrupados por plan_id):
  a. SQLite — upsert sessions/turns/plans (igual que M2)
  b. Por cada evento individual:
       extracted = extractor.extract(event)
       if extracted:
           zep_client.add_memory(session_id, extracted["messages"])
  c. mark_processed solo si AMBOS (a) y (b) tuvieron éxito —
     el WAL es la fuente de verdad para reintentos.

El Bibliotecario no bloquea al Director. Es infraestructura paralela,
no un Worker, no invocable por el Planner.
"""

import logging
import os
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional

from memory.wal.wal_manager import WALManager
from memory.sqlite.writer import SQLiteWriter
from memory.bibliotecario.extractor import Extractor
from memory.zep.zep_client import ZepClient

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 60

# Module-level singletons — initialised once in start(), reused across
# every run() call (both the initial pass and the polling loop).
_wal: Optional[WALManager] = None
_writer: Optional[SQLiteWriter] = None
_extractor: Optional[Extractor] = None
_zep: Optional[ZepClient] = None


def _init_singletons() -> None:
    global _wal, _writer, _extractor, _zep
    _wal = WALManager()
    _writer = SQLiteWriter()
    _extractor = Extractor()
    _zep = ZepClient(
        api_key=os.environ["ZEP_API_KEY"],
        # base_url omitted — defaults to Zep Cloud
    )


def run() -> int:
    """
    Processes all pending WAL events. Returns the number of events processed.

    Idempotent — safe to call from both the initial pass and the polling
    loop. If no events are pending, returns 0 immediately.
    """
    assert _wal is not None, "Bibliotecario not started. Call start() first."
    assert _writer is not None, "Bibliotecario not started. Call start() first."
    assert _extractor is not None, "Bibliotecario not started. Call start() first."
    assert _zep is not None, "Bibliotecario not started. Call start() first."

    pending = _wal.unprocessed()
    if not pending:
        return 0

    # Group by plan_id — one plan may have multiple memory_critical events
    plans: Dict[str, List[dict]] = defaultdict(list)
    for event in pending:
        plans[event["plan_id"]].append(event)

    total_processed = 0

    for plan_id, events in plans.items():
        first = events[0]
        session_id = first["session_id"]
        turn_id = first["turn_id"]
        task = first["task"]

        # --- SQLite write (synchronous, immediate) ---
        try:
            existing = _writer._conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not existing:
                _writer._conn.execute(
                    "INSERT INTO sessions (id, domain, ts_start) VALUES (?, 'dev', ?)",
                    (session_id, first["ts"]),
                )
                _writer._conn.commit()

            existing_turn = _writer._conn.execute(
                "SELECT id FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
            if not existing_turn:
                _writer.write_turn(
                    session_id=session_id,
                    input_text=task,
                    turn_id_override=turn_id,
                )

            worker_sequence = [e["worker"] for e in events]

            existing_plan = _writer._conn.execute(
                "SELECT id FROM plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if not existing_plan:
                _writer.open_plan(turn_id=turn_id, plan_id=plan_id, task=task)

            _writer.close_plan(
                plan_id=plan_id,
                status="DONE",
                worker_sequence=worker_sequence,
            )
        except Exception as e:
            logger.error(
                "[Bibliotecario] SQLite write failed for plan %s: %s", plan_id, e
            )
            # Leave events unprocessed — next poll cycle will retry.
            continue

        # --- Zep write (per individual event) ---
        # Only mark_processed if BOTH SQLite and Zep succeed for an event.
        for event in events:
            extracted = _extractor.extract(event)
            if extracted is None:
                # Extractor logged the error. Leave unprocessed for retry.
                logger.warning(
                    "[Bibliotecario] skipping Zep write for event %s — extractor returned None",
                    event["event_id"],
                )
                continue

            try:
                _zep.add_memory(session_id, extracted["messages"])
            except Exception as e:
                logger.error(
                    "[Bibliotecario] Zep write failed for event %s: %s",
                    event["event_id"],
                    e,
                )
                # Leave unprocessed for retry.
                continue

            # Both SQLite and Zep succeeded — safe to mark processed.
            _wal.mark_processed(event["event_id"])
            total_processed += 1

    return total_processed


def _poll_loop() -> None:
    """
    Background polling loop. Runs every 60 seconds.
    Never propagates exceptions — a transient failure in run() should
    not kill the poll thread, which would leave events unprocessed forever.
    """
    while True:
        time.sleep(_POLL_INTERVAL_SECONDS)
        try:
            count = run()
            if count:
                logger.info(
                    "[Bibliotecario] poll processed %d event(s)", count)
        except Exception as e:
            logger.error("[Bibliotecario] poll error: %s", e)


def start() -> int:
    """
    Called once from cli.py at Nova startup.

    1. Initialises module singletons (WALManager, SQLiteWriter,
       Extractor, ZepClient).
    2. Runs an initial synchronous pass — processes any events left
       unprocessed from previous sessions.
    3. Calls compact() — removes processed events from the WAL file.
       (stub for now, no-op until implemented)
    4. Starts the background polling thread (daemon — dies with the
       main process, no cleanup needed).

    Returns the number of events processed in the initial pass.
    """
    _init_singletons()
    assert _wal is not None, "Bibliotecario not started. Call start() first."

    count = run()

    _wal.compact()

    thread = threading.Thread(
        target=_poll_loop, daemon=True, name="bibliotecario-poll")
    thread.start()
    logger.info(
        "[Bibliotecario] polling thread started (interval: %ds)", _POLL_INTERVAL_SECONDS)

    return count

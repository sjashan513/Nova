"""
core/init/nova_boot.py — arranque de subsistemas al iniciar Nova.

Responsabilidad única: poner en marcha la infraestructura que Nova
necesita antes de procesar cualquier input. No sabe nada del loop
del CLI ni de la capa conversacional.

Subsistemas arrancados:
  1. WAL recovery   — reprocesa eventos no procesados de sesiones anteriores
  2. Bibliotecario  — pasada inicial síncrona + polling thread daemon
"""

from memory.wal.wal_reader import WALReader
from memory.bibliotecario import bibliotecario


def boot() -> None:
    """
    Arranca los subsistemas de Nova en el orden correcto.
    Llamar una sola vez al inicio de cli.py, antes del loop principal.
    """
    _recover_wal()
    _start_bibliotecario()


def _recover_wal() -> None:
    reader = WALReader()
    pending = list(reader.unprocessed())
    if pending:
        print(
            f"[WAL] {len(pending)} evento(s) no procesado(s) detectado(s) al arrancar:")
        for e in pending:
            print(f"  - {e['worker']} / {e['project']} / ts={e['ts']}")


def _start_bibliotecario() -> None:
    processed = bibliotecario.start()
    if processed:
        print(f"[Bibliotecario] {processed} evento(s) escritos en SQLite.")

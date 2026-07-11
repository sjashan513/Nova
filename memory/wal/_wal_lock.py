"""
WAL subsystem lock.

Single threading.Lock for all WAL operations. Every read and write
to nova_wal.jsonl must be held under this lock — WALWriter.append()
rewrites the file with write_text(), which would corrupt an in-progress
append() from the Director if they ran concurrently without coordination.

Private to memory/wal/. Nothing outside this subsystem should import
this directly — use WALManager instead.
"""

import threading

WAL_LOCK = threading.Lock()

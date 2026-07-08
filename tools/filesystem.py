"""
Filesystem primitive tool -- no LLM involved.

Each function returns a plain dict whose keys are exactly what
core/director/context.py needs to resolve "$step_id.field" references
in later steps (e.g. {"content": ..., "path": ...} lets a later step
use "$s1.content" or "$s1.path"). The keys returned here ARE the
contract -- if a key is renamed, every prompt and every hand-written
test Plan that references it breaks.

Sealed shapes (Fase 2 design session):
    read(path)          -> {"content": str, "path": str}
    write(path, content) -> {"path": str, "bytes_written": int}
    list_dir(path)        -> {"path": str, "entries": List[str]}

These functions do NOT catch and wrap filesystem errors (FileNotFoundError,
PermissionError, IsADirectoryError, etc.) -- they let them propagate as
the real, original Python exception. Wrapping into StepExecutionError
is core/director/error_policy.py's job (it wraps whatever exception a
callable raises, not specifically a filesystem one) -- duplicating that
wrapping here would mean two layers disagreeing about what the wrapped
exception looks like.
"""

import os
from typing import Any, Dict, List


def read(path: str) -> Dict[str, Any]:
    """
    Reads a text file. Raises FileNotFoundError, PermissionError,
    IsADirectoryError, or UnicodeDecodeError as-is on failure -- see
    module docstring for why these are not caught here.
    """
    path = os.path.expanduser(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    return {"content": content, "path": path}


def write(path: str, content: str) -> Dict[str, Any]:
    """
    Writes text to a file, creating it if it doesn't exist and
    overwriting it if it does. Does NOT create missing parent
    directories -- a path with a nonexistent parent raises
    FileNotFoundError, same as the underlying `open()` call would.
    """
    path = os.path.expanduser(path)
    with open(path, "w", encoding="utf-8") as f:
        bytes_written = f.write(content)

    return {"path": path, "bytes_written": bytes_written}


def list_dir(path: str) -> Dict[str, Any]:
    """
    Lists the entries of a directory (not recursive). Raises
    FileNotFoundError or NotADirectoryError as-is on failure.
    """
    path = os.path.expanduser(path)
    entries: List[str] = os.listdir(path)

    return {"path": path, "entries": entries}

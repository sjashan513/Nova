"""
tools/vscode.py — VSCode diff gate

The only public function here is show_diff(). It is the sole mechanism
by which Nova proposes code changes to Jashan. Nova never writes code
directly to disk — every proposed change goes through this gate first.

Flow:
  1. Write proposed_content to a .nova_<filename>.tmp file alongside
     the original.
  2. POST /diff/show to the VSCode extension — it opens the Diff Editor.
  3. Poll GET /diff/status every 2s until accepted, rejected, or timeout.
  4. accepted → write proposed_content to the real file, delete tmp,
                return {"decision": "accepted", "applied": True}
  5. rejected → delete tmp,
                raise PlanAbortedError  (conscious human decision, not a bug)
  6. timeout  → delete tmp,
                raise WorkerExecutionError  (no decision in 300s)

The extension owns the UI (Diff Editor, keybindings). This module owns
the file I/O and the blocking wait. Neither side writes the other's
artifacts.

Only workers not-LLM can be called internally by another worker
(Fase 4 principle). This is a primitive tool, not a worker — it is
called directly by the Director via _TOOL_DISPATCH.
"""

import os
import time
import requests

from core.domain.exceptions.execution_errors import WorkerExecutionError, PlanAbortedError

# The VSCode extension's local HTTP server.
# Port 3333 is fixed — see vscode-extension/src/server.ts.
_EXTENSION_BASE = "http://localhost:3333"

# Polling interval and total timeout for a human diff decision.
# 2s poll is fast enough to feel responsive; 300s gives Jashan
# time to actually read and review the diff without Nova timing out.
_POLL_INTERVAL_S = 2
_TIMEOUT_S = 300


def show_diff(file_path: str, proposed_content: str) -> dict:
    """
    Proposes a code change to Jashan via the VSCode Diff Editor and
    blocks until he accepts or rejects it.

    Args:
        file_path:        Absolute path to the file being modified.
                          Must already exist — this is a change proposal,
                          not a new-file creation.
        proposed_content: The full new content of the file.

    Returns:
        {"decision": "accepted", "applied": True}

    Raises:
        WorkerExecutionError: Could not reach the VSCode extension, or
                              Jashan did not respond within 300s.
        PlanAbortedError:     Jashan explicitly rejected the diff.
                              This is not a bug — the plan stops cleanly.
    """
    file_path = os.path.expanduser(file_path)

    # --- 1. Write the tmp file ---
    dir_name = os.path.dirname(file_path)
    base_name = os.path.basename(file_path)
    tmp_path = os.path.join(dir_name, f".nova_{base_name}.tmp")

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(proposed_content)
    except OSError as e:
        raise WorkerExecutionError(
            reason=f"Failed to write tmp file '{tmp_path}': {e}",
        )

    # --- 2. Notify the extension ---
    try:
        resp = requests.post(
            f"{_EXTENSION_BASE}/diff/show",
            json={"file_path": file_path, "tmp_path": tmp_path},
            timeout=5,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        _safe_delete(tmp_path)
        raise WorkerExecutionError(
            reason=(
                f"Could not reach VSCode extension at {_EXTENSION_BASE}: {e}. "
                "Is the Nova VSCode extension running? Check the status bar."
            ),
        )

    # --- 3. Poll for decision ---
    deadline = time.monotonic() + _TIMEOUT_S

    while True:
        if time.monotonic() > deadline:
            _safe_delete(tmp_path)
            raise WorkerExecutionError(
                reason=(
                    f"Diff decision timeout: no response after {_TIMEOUT_S}s. "
                    "The plan has been stopped. Run the task again when ready."
                ),
            )

        time.sleep(_POLL_INTERVAL_S)

        try:
            resp = requests.get(
                f"{_EXTENSION_BASE}/diff/status",
                timeout=3,
            )
            resp.raise_for_status()
            status = resp.json().get("status", "pending")
        except requests.RequestException:
            # Extension may be momentarily busy — skip this tick, keep waiting.
            continue

        if status == "accepted":
            # Write the real file only on explicit acceptance.
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(proposed_content)
            except OSError as e:
                _safe_delete(tmp_path)
                raise WorkerExecutionError(
                    reason=f"Diff accepted but failed to write '{file_path}': {e}",
                )
            _safe_delete(tmp_path)
            return {"decision": "accepted", "applied": True}

        elif status == "rejected":
            _safe_delete(tmp_path)
            raise PlanAbortedError(
                f"Diff rejected by user for '{base_name}'. "
                "Plan aborted cleanly."
            )

        # status == "pending" → keep polling


def _safe_delete(path: str) -> None:
    """
    Deletes a file without raising if it is already gone.
    Used to clean up .tmp files in every exit path of show_diff —
    accepted, rejected, timeout, or extension-unreachable.
    """
    try:
        os.remove(path)
    except FileNotFoundError:
        pass

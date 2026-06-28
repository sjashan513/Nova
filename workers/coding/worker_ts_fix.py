"""
worker_ts_fix — fixes a specific set of TypeScript compiler errors in
a file's content, with minimal changes only (per
registry/tool_registry.yaml's description for this entry). LLM-
powered (requires_model: true) -- temperature defaults low (0.1) since
this is a deterministic correction task, not a creative one. NOT
confirmed by Jashan as a sealed value this session -- flagged as this
Worker's own proposed default, overridable via input["temperature"] if
a different value is ever needed.

Workers think, Tools do I/O (sealed architectural invariant): this
Worker does NOT read the file from disk itself, and does NOT write the
fix back to disk. It receives file content as plain text (typically
via a Step input referencing "$<filesystem_read_step>.content") and
returns the fixed content as plain text -- writing it back is a
separate filesystem.write Step that depends on this one and
references "$<this_step>.fixed_content". Same reasoning applies to
"errors": typically populated via "$<worker_ts_check_step>.errors" so
this Worker fixes EXACTLY what a prior check found, never open-ended
review of the whole file.

Result contract (sealed this session):
    {"fixed_content": str, "changes_made": List[str]}
"""

from typing import Any, Dict, List, Optional

import requests

from core.llm.nim_client import call_nim, parse_json_response
from registry.project_registry import project_exists, list_project_names
from workers.base import BaseWorker, WorkerOutput

_DEFAULT_TEMPERATURE = 0.1

_SYSTEM_PROMPT = """You are a TypeScript error-fixing assistant. You receive a file's \
full content and a list of specific compiler errors. Your job is to \
fix ONLY those errors, with the smallest possible change to make each \
one go away -- never refactor, rename, reformat, or otherwise change \
anything beyond what is strictly necessary to resolve the listed \
errors.

Respond with ONLY valid JSON, no prose, no markdown code fences, in \
exactly this shape:
{"fixed_content": "<the full corrected file content>", "changes_made": ["<short description of each change>", "..."]}

"fixed_content" must be the COMPLETE file, not a diff or a snippet -- \
every line of the original file you did not need to touch must appear \
unchanged.
"""


def _build_user_content(file_content: str, errors: List[Dict[str, Any]]) -> str:
    """
    Formats the file content and the specific errors to fix into the
    user message sent to the model. `errors` is expected in
    worker_ts_check's own result shape ({"file", "line", "column",
    "code", "message"}) -- the natural source being a prior
    worker_ts_check step's result, referenced via depends_on/input.
    """
    error_lines = [
        f"- Line {e.get('line')}, column {e.get('column')}: "
        f"[{e.get('code')}] {e.get('message')}"
        for e in errors
    ]
    return (
        "FILE CONTENT:\n"
        f"{file_content}\n\n"
        "ERRORS TO FIX:\n" + "\n".join(error_lines)
    )


class WorkerTsFix(BaseWorker):
    """
    LLM-powered Worker. Input contract:
      - "project": exact registered project name (Planner prompt rule
        -- see core/planner/planner_prompt.py). Validated here too,
        defense in depth, even though this Worker doesn't touch the
        filesystem directly -- it still needs the project to exist as
        a sanity check that the task is scoped to a real codebase.
      - "model": injected by the Director right before dispatch (see
        core/director/director_instance.py::_execute_step) -- never
        supplied by Kimi directly on the Step itself. Every LLM-
        powered worker in Fase 3 shares this same expectation.
      - "file_content": the full text of the file to fix (str).
      - "errors": a non-empty list of error dicts (worker_ts_check's
        result shape) describing exactly what to fix. Required, not
        optional -- this Worker fixes what it's told, it does not go
        looking for problems on its own (see module docstring).
      - "temperature" (optional): override for _DEFAULT_TEMPERATURE.
    """

    def execute(self, input: Dict[str, Any]) -> WorkerOutput:
        project_name = input.get("project")
        if project_name is None:
            return {
                "status": "error",
                "result": None,
                "reason": "Missing required 'project' key in input.",
            }
        if not project_exists(project_name):
            return {
                "status": "error",
                "result": None,
                "reason": (
                    f"Unknown project '{project_name}'. Available "
                    f"projects: {', '.join(list_project_names())}"
                ),
            }

        model = input.get("model")
        if not model:
            return {
                "status": "error",
                "result": None,
                "reason": "Missing required 'model' key in input.",
            }

        file_content = input.get("file_content")
        if file_content is None:
            return {
                "status": "error",
                "result": None,
                "reason": "Missing required 'file_content' key in input.",
            }

        errors = input.get("errors")
        if not errors:
            return {
                "status": "error",
                "result": None,
                "reason": (
                    "Missing or empty 'errors' key in input -- this "
                    "Worker fixes specific, named errors only, it does "
                    "not perform open-ended review."
                ),
            }

        temperature: float = input.get("temperature", _DEFAULT_TEMPERATURE)

        try:
            raw = call_nim(
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_content=_build_user_content(file_content, errors),
                temperature=temperature,
            )
        except (RuntimeError, requests.exceptions.RequestException, KeyError, IndexError) as e:
            # Known failure modes call_nim itself documents (missing
            # API key, network/HTTP failure, malformed response shape)
            # -- caught here specifically, not via a blanket
            # `except Exception`, so a real bug in this Worker's own
            # code is never silently swallowed as "the model failed."
            return {
                "status": "error",
                "result": None,
                "reason": f"Model call failed: {type(e).__name__}: {e}",
            }

        try:
            parsed = parse_json_response(
                raw, required_keys=["fixed_content", "changes_made"]
            )
        except ValueError as e:
            return {
                "status": "error",
                "result": None,
                "reason": str(e),
            }

        return {
            "status": "success",
            "result": {
                "fixed_content": parsed["fixed_content"],
                "changes_made": parsed["changes_made"],
            },
            "reason": None,
        }

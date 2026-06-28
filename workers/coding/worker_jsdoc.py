"""
worker_jsdoc — adds JSDoc comments to exported symbols in a file's
content. Never touches logic (per registry/tool_registry.yaml's
description for this entry) -- this Worker only adds documentation
comments, it must never modify a single line of actual code.

Same "Workers think, Tools do I/O" split as worker_ts_fix: receives
file content as plain text (typically "$<filesystem_read_step>.content"),
returns documented content as plain text. Writing it back to disk is a
separate filesystem.write Step depending on this one.

Result contract (sealed this session):
    {"documented_content": str, "symbols_documented": List[str]}
"""

from typing import Any, Dict, List

import requests

from core.llm.nim_client import call_nim, parse_json_response
from registry.project_registry import project_exists, list_project_names
from workers.base import BaseWorker, WorkerOutput

_DEFAULT_TEMPERATURE = 0.1

_SYSTEM_PROMPT = """You are a JSDoc documentation assistant. You receive a file's full \
content. Your job is to add JSDoc comments to every exported symbol \
(functions, classes, types, interfaces, consts) that does not already \
have one. You must NEVER change, reformat, or remove a single line of \
actual code -- only insert JSDoc comment blocks above the symbols that \
need them. Symbols that already have a JSDoc comment are left \
untouched, not rewritten or "improved".

Respond with ONLY valid JSON, no prose, no markdown code fences, in \
exactly this shape:
{"documented_content": "<the full file content with JSDoc added>", "symbols_documented": ["<symbol name>", "..."]}

"documented_content" must be the COMPLETE file, not a diff or a \
snippet -- every line of the original file appears exactly as it was, \
with only JSDoc blocks inserted above the symbols that needed them.
"""


def _build_user_content(file_content: str) -> str:
    return f"FILE CONTENT:\n{file_content}"


class WorkerJsdoc(BaseWorker):
    """
    LLM-powered Worker. Input contract:
      - "project": exact registered project name. Validated here too,
        defense in depth (same reasoning as WorkerTsFix).
      - "model": injected by the Director right before dispatch.
      - "file_content": the full text of the file to document (str).
      - "temperature" (optional): override for _DEFAULT_TEMPERATURE.

    No "errors" field, unlike WorkerTsFix -- this Worker has no
    specific target list to work from; "every undocumented exported
    symbol in this file" IS its scope, by design (contrast with
    WorkerTsFix, which deliberately refuses to run without a specific
    error list -- the two Workers have genuinely different scopes,
    not an oversight here).
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

        temperature: float = input.get("temperature", _DEFAULT_TEMPERATURE)

        try:
            raw = call_nim(
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_content=_build_user_content(file_content),
                temperature=temperature,
            )
        except (RuntimeError, requests.exceptions.RequestException, KeyError, IndexError) as e:
            return {
                "status": "error",
                "result": None,
                "reason": f"Model call failed: {type(e).__name__}: {e}",
            }

        try:
            parsed = parse_json_response(
                raw, required_keys=["documented_content", "symbols_documented"]
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
                "documented_content": parsed["documented_content"],
                "symbols_documented": parsed["symbols_documented"],
            },
            "reason": None,
        }

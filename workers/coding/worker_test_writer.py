"""
worker_test_writer — writes unit tests for a file's content. Language-
agnostic (per registry/tool_registry.yaml's description for this
entry): does not assume TypeScript/Python/any specific language or
framework in its own logic -- it reads the project's "lang" from
registry/project_registry.yaml (already available, not a new contract
field) and tells the model what language to write in, leaving
framework-idiom choice to the model's own judgment (or to conventions
it can infer from the file's existing imports).

Same "Workers think, Tools do I/O" split as worker_ts_fix/worker_jsdoc:
returns test code as plain text; writing it to a test file on disk is
a separate filesystem.write Step depending on this one.

Result contract (sealed this session):
    {"test_content": str, "test_count": int}
test_count is the MODEL's own self-reported count of test cases
written, not independently verified by parsing test_content -- doing
that reliably would require language/framework-specific parsing logic,
which would contradict this Worker's language-agnostic design. This is
a known, accepted limitation, not a contract violation: same category
of trust Nova already places in Kimi self-reporting plan status.
"""

from typing import Any, Dict

import requests

from core.llm.nim_client import call_nim, parse_json_response
from registry.project_registry import project_exists, get_project, list_project_names
from workers.base import BaseWorker, WorkerOutput

_DEFAULT_TEMPERATURE = 0.2

_SYSTEM_PROMPT = """You are a test-writing assistant. You receive a file's full content \
and the language it is written in. Your job is to write a \
comprehensive set of unit tests covering the file's exported \
functions/classes -- normal cases, edge cases, and error cases where \
applicable. Use idiomatic testing conventions for the given language \
unless the file's own existing imports or conventions suggest a \
specific framework already in use, in which case follow that instead.

Respond with ONLY valid JSON, no prose, no markdown code fences, in \
exactly this shape:
{"test_content": "<the complete test file content>", "test_count": <number of individual test cases written, as an integer>}
"""


def _build_user_content(file_content: str, language: str) -> str:
    return f"LANGUAGE: {language}\n\nFILE CONTENT:\n{file_content}"


class WorkerTestWriter(BaseWorker):
    """
    LLM-powered Worker. Input contract:
      - "project": exact registered project name. Used here not just
        as a sanity check (defense in depth, same as every other
        Worker) but for real content: its "lang" field is what tells
        the model what language to write tests in.
      - "model": injected by the Director right before dispatch.
      - "file_content": the full text of the file to write tests for.
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

        language = get_project(project_name).get(
            "lang", "the project's language")
        temperature: float = input.get("temperature", _DEFAULT_TEMPERATURE)

        try:
            raw = call_nim(
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_content=_build_user_content(file_content, language),
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
                raw, required_keys=["test_content", "test_count"]
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
                "test_content": parsed["test_content"],
                "test_count": parsed["test_count"],
            },
            "reason": None,
        }

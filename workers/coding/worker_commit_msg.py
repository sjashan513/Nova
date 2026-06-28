"""
worker_commit_msg — generates a conventional-commit-style message from
a diff. Result contract is deliberately minimal (sealed this session):
a single formatted string, not type/scope/description as separate
fields -- Jashan confirmed this minimalist shape explicitly rather
than a structured breakdown.

This Worker does not run `git diff` itself (Workers think, Tools do
I/O) -- the diff text is expected as input, typically via
"$<terminal_run_step>.stdout" from a Step that ran `git diff` through
the terminal tool.

Result contract (sealed this session):
    {"message": str}
"""

from typing import Any, Dict

import requests

from core.llm.nim_client import call_nim, parse_json_response
from registry.project_registry import project_exists, list_project_names
from workers.base import BaseWorker, WorkerOutput

_DEFAULT_TEMPERATURE = 0.2

# Timeout raised for safety, same NIM latency observed across every
# other LLM worker this session -- but NOT max_tokens: a commit
# message is one short line, not a full file, no reason to ask for
# 16k tokens of room it will never use.
_DEFAULT_TIMEOUT_SECONDS = 120

_SYSTEM_PROMPT = """You are a commit message assistant. You receive a git diff. Your job \
is to write a single conventional-commit-style message summarizing \
the change: "<type>(<scope>): <short description>", where <type> is \
one of feat, fix, refactor, docs, test, chore, style, perf, and \
<scope> is the affected module/file/area in a few words or omitted \
entirely if the change is broad. Keep the description under 72 \
characters where possible. Do not invent changes not present in the \
diff; do not describe the diff line by line -- summarize its overall \
intent.

Respond with ONLY valid JSON, no prose, no markdown code fences, in \
exactly this shape:
{"message": "<type>(<scope>): <description>"}
"""


def _build_user_content(diff: str) -> str:
    return f"DIFF:\n{diff}"


class WorkerCommitMsg(BaseWorker):
    """
    LLM-powered Worker. Input contract:
      - "project": exact registered project name. Validated here too,
        defense in depth, even though this Worker's own logic doesn't
        touch the filesystem directly -- same consistency rule applied
        to every worker_* Step (see core/planner/planner_prompt.py).
      - "model": injected by the Director right before dispatch.
      - "diff": the full text of a git diff (str) -- the change to
        summarize.
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

        diff = input.get("diff")
        if not diff:
            return {
                "status": "error",
                "result": None,
                "reason": "Missing or empty 'diff' key in input.",
            }

        temperature: float = input.get("temperature", _DEFAULT_TEMPERATURE)
        timeout: float = input.get("timeout", _DEFAULT_TIMEOUT_SECONDS)

        try:
            raw = call_nim(
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_content=_build_user_content(diff),
                temperature=temperature,
                timeout=timeout,
            )
        except (RuntimeError, requests.exceptions.RequestException, KeyError, IndexError) as e:
            return {
                "status": "error",
                "result": None,
                "reason": f"Model call failed: {type(e).__name__}: {e}",
            }

        try:
            parsed = parse_json_response(raw, required_keys=["message"])
        except ValueError as e:
            return {
                "status": "error",
                "result": None,
                "reason": str(e),
            }

        return {
            "status": "success",
            "result": {"message": parsed["message"]},
            "reason": None,
        }

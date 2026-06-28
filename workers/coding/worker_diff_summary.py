"""
worker_diff_summary — plain-language summary of a diff (per
registry/tool_registry.yaml's description for this entry). Distinct
purpose from worker_commit_msg despite a similar input shape: this
Worker explains WHAT changed and WHY it likely matters, in normal
prose for a human reading a PR description or changelog -- not a
single conventional-commit line.

Same "diff comes in as input, not run by this Worker" pattern as
worker_commit_msg -- typically populated via
"$<terminal_run_step>.stdout" from a `git diff` Step.

Result contract (sealed this session):
    {"summary": str}
"""

from typing import Any, Dict

import requests

from core.llm.nim_client import call_nim, parse_json_response
from registry.project_registry import project_exists, list_project_names
from workers.base import BaseWorker, WorkerOutput

_DEFAULT_TEMPERATURE = 0.2

_SYSTEM_PROMPT = """You are a code-change summarization assistant. You receive a git \
diff. Your job is to write a short, plain-language summary (2-5 \
sentences) explaining what changed and why it likely matters, for a \
human reading a PR description or changelog -- not a commit message, \
not a line-by-line walkthrough. Focus on the overall intent and \
user-visible or behavioral impact of the change, not implementation \
mechanics, unless the mechanics ARE the point (e.g. a refactor with no \
behavior change should say so explicitly).

Respond with ONLY valid JSON, no prose, no markdown code fences, in \
exactly this shape:
{"summary": "<the plain-language summary>"}
"""


def _build_user_content(diff: str) -> str:
    return f"DIFF:\n{diff}"


class WorkerDiffSummary(BaseWorker):
    """
    LLM-powered Worker. Input contract:
      - "project": exact registered project name. Validated here too,
        defense in depth, same consistency rule applied to every
        worker_* Step.
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

        try:
            raw = call_nim(
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_content=_build_user_content(diff),
                temperature=temperature,
            )
        except (RuntimeError, requests.exceptions.RequestException, KeyError, IndexError) as e:
            return {
                "status": "error",
                "result": None,
                "reason": f"Model call failed: {type(e).__name__}: {e}",
            }

        try:
            parsed = parse_json_response(raw, required_keys=["summary"])
        except ValueError as e:
            return {
                "status": "error",
                "result": None,
                "reason": str(e),
            }

        return {
            "status": "success",
            "result": {"summary": parsed["summary"]},
            "reason": None,
        }

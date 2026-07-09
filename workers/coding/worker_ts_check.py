"""
worker_ts_check — runs `tsc --noEmit` against a registered project and
returns its errors as structured data. No LLM: same category as
tools/terminal.py's terminal.run (subprocess + deterministic parsing),
packaged as a Worker rather than a primitive tool because it is
domain-specific (TypeScript compilation, not a generic command), per
registry/tool_registry.yaml's requires_model: false for this entry.

Result contract (sealed this session, NOVA_WORKER_LAYER_ADR.md /
session notes):
    {
      "errors": [
        {"file": str, "line": int, "column": int, "code": str, "message": str},
        ...
      ],
      "error_count": int
    }
error_count is always len(errors), computed here rather than parsed
from tsc's own "Found N errors." summary line -- this guarantees the
two numbers can never disagree, and means Kimi can reference
$step_id.error_count directly without that count depending on a second,
separate parse of a different line of output.
"""

import re
import subprocess
from typing import Any, Dict, List

from registry.project_registry import project_exists, get_project, list_project_names
from workers.base import BaseWorker, WorkerOutput

# tsc's plain-text format with --pretty false, one error per line:
#   <file>(<line>,<column>): error <code>: <message>
# Lines that don't match this shape (blank lines, the trailing
# "Found N errors." summary) are silently skipped in
# _parse_tsc_output -- they carry no structured information this
# contract cares about.
_TSC_ERROR_PATTERN = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<column>\d+)\): "
    r"error (?P<code>TS\d+): (?P<message>.+)$"
)

_TSC_TIMEOUT_SECONDS = 120


def _parse_tsc_output(stdout: str) -> List[Dict[str, Any]]:
    """
    Parses tsc --noEmit --pretty false stdout into the structured
    error list this Worker's result contract promises. Returns an
    empty list if no lines match -- this is the expected shape for
    BOTH a clean compile and (handled separately, see execute() below)
    a case where tsc never actually ran; this function alone cannot
    tell those two apart, which is exactly why execute() checks
    returncode/stderr before trusting an empty list as "0 errors".
    """
    errors: List[Dict[str, Any]] = []
    for line in stdout.splitlines():
        match = _TSC_ERROR_PATTERN.match(line.strip())
        if not match:
            continue
        errors.append(
            {
                "file": match.group("file"),
                "line": int(match.group("line")),
                "column": int(match.group("column")),
                "code": match.group("code"),
                "message": match.group("message"),
            }
        )
    return errors


class WorkerTsCheck(BaseWorker):
    """
    No-LLM Worker. execute() never calls core/llm/nim_client.py --
    everything here is subprocess + text parsing, same as
    tools/terminal.py. Inherits from BaseWorker anyway (not a free
    function) because the Director's dispatch contract is uniform
    across every worker_* entry, LLM-powered or not -- see
    workers/base.py's docstring: "BaseWorker does NOT assume LLM
    usage."
    """

    def execute(self, input: Dict[str, Any]) -> WorkerOutput:
        """
        Expects input["project"] -- the exact name of a registered
        project (see registry/project_registry.yaml), per the Planner
        prompt rule requiring every worker_* step to declare one.
        Defense in depth: this is checked here too, independently of
        core/planner/validators.py::validate_step_projects_exist --
        a Worker should never assume contract validation upstream
        already caught everything (same principle
        core/director/error_policy.py::execute_with_retry already
        follows by never assuming `fn` can only fail in expected
        ways).
        """
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

        project_path = get_project(project_name)["path"]

        # If a specific file_path is provided, pass it directly to tsc
        # instead of compiling the whole project. Two reasons:
        #   1. Correctness — only errors for that file are returned,
        #      no contamination from other files in the project.
        #   2. Performance — on large repos, compiling everything just
        #      to check one file is wasteful and slow.
        # file_path must be absolute — the Planner always constructs it
        # as <project.path>/<file>, never as a relative path.
        # When no file_path is given, tsc compiles the full project as
        # before — useful for whole-project health checks.
        import os
        file_path = input.get("file_path")
        tsc_args = ["npx", "tsc", "--noEmit", "--pretty", "false"]
        if file_path:
            # Pass the file directly to tsc — it compiles only that file,
            # ignoring tsconfig.json's include/exclude globs. This means
            # tsc runs without the project's tsconfig, so we add the minimum
            # flags to avoid false positives from missing lib definitions.
            # --skipLibCheck silences errors in .d.ts files that aren't
            # our concern here. --target ES2020 avoids "lib not found" errors.
            tsc_args = [
                "npx", "tsc",
                "--noEmit",
                "--pretty", "false",
                "--skipLibCheck",
                "--target", "ES2020",
                "--strict",
                file_path,
            ]

        try:
            completed = subprocess.run(
                tsc_args,
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=_TSC_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "result": None,
                "reason": (
                    f"tsc timed out after {_TSC_TIMEOUT_SECONDS}s on "
                    f"project '{project_name}'."
                ),
            }
        except OSError as e:
            # npx itself missing/unreachable, or project_path doesn't
            # exist on disk -- a real environment problem, not a
            # TypeScript problem. Caught here (not left to propagate
            # as a raw OSError) so it becomes a normal status: "error"
            # the Director's error policy already knows how to handle,
            # same as any other Worker failure.
            return {
                "status": "error",
                "result": None,
                "reason": (
                    f"Failed to run tsc on project '{project_name}' "
                    f"(path: {project_path}): {e}"
                ),
            }

        errors = _parse_tsc_output(completed.stdout)

        if completed.returncode != 0 and not errors and completed.stderr.strip():
            # tsc exited non-zero, produced no parseable type errors,
            # AND wrote to stderr -- this is NOT a clean compile, it's
            # tsc itself failing to run (binary not found, missing
            # tsconfig.json, a config error). Reporting error_count: 0
            # here would silently tell Kimi/Jashan the code is clean
            # when nothing was actually checked -- the exact kind of
            # silent failure the rest of this codebase avoids
            # everywhere else (PlanContractErrorGroup, RetriesExhausted
            # Error: never lose information about what was tried).
            return {
                "status": "error",
                "result": None,
                "reason": (
                    f"tsc failed to run on project '{project_name}': "
                    f"{completed.stderr.strip()}"
                ),
            }

        return {
            "status": "success",
            "result": {"errors": errors, "error_count": len(errors)},
            "reason": None,
        }

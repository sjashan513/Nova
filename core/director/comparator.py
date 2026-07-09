"""
Comparator — pre-execution assume checker.

Runs before every step dispatch in DirectorInstance. Reads implicit_assumes
from the tool/worker registry entry for that step, evaluates each one
deterministically against the current plan context and filesystem state,
and returns a ComparatorResult.

The Director never calls this for steps that are already FAILED or SKIPPED.
It only runs for steps about to transition from "pending" to "in_progress".

No LLM involvement. No side effects. Pure read + evaluation.
"""

import hashlib
import shutil
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from core.domain.models import Plan, Step


# ---------------------------------------------------------------------------
# Output contracts
# ---------------------------------------------------------------------------

@dataclass
class AssumeFailure:
    op: str            # "FILE_EXISTS", "FILE_UNCHANGED", etc.
    reason: str        # human-readable message for the CLI
    assume: Dict       # the raw assume dict from the registry


@dataclass
class ComparatorResult:
    passed: bool
    failed_assumes: List[AssumeFailure] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reference resolver
# ---------------------------------------------------------------------------

_REF_PATTERN = re.compile(r'^\$([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)$')


def _resolve_ref(ref_value: str, context: Dict[str, Any]) -> Optional[Any]:
    """
    Resolves a "$step_id.field" reference against the plan context.
    Returns None if the reference can't be resolved (step not yet done,
    field doesn't exist, or value is not a reference string at all).

    Only resolves exact references — a string that IS the reference,
    not one that contains it embedded in other text (same rule as context.py).
    """
    if not isinstance(ref_value, str):
        return None
    match = _REF_PATTERN.match(ref_value.strip())
    if not match:
        return None
    step_id: str = match.group(1)
    field_name: str = match.group(2)
    step_result = context.get(step_id)
    if not step_result:
        return None
    return step_result.get(field_name)


def _extract_step_id(ref_value: str) -> Optional[str]:
    """
    Extracts just the step_id from a "$step_id.field" string.
    Returns None if not a valid reference.
    """
    if not isinstance(ref_value, str):
        return None
    match = _REF_PATTERN.match(ref_value.strip())
    return str(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Parameter resolution helpers
# ---------------------------------------------------------------------------

def _resolve_param(param: str, step: Step, context: Dict[str, Any],
                   projects: Dict[str, Any]) -> Optional[str]:
    """
    Resolves a registry parameter string like "input.path", "input.file_path",
    "project.path" against the concrete step and plan context.

    Supported forms:
      input.<key>   → step.input[key], then resolved against context if it's a ref
      project.path  → looks up the project name from step.input["project"]
                      in the projects dict (from project_registry.yaml,
                      passed directly — Plan is never mutated)
    """
    if param.startswith("input."):
        key = param[len("input."):]
        raw = step.input.get(key)
        if raw is None:
            return None
        # If the value is a "$step_id.field" reference, resolve it
        resolved = _resolve_ref(str(raw), context)
        return str(resolved) if resolved is not None else str(raw)

    if param == "project.path":
        project_name = step.input.get("project")
        if not project_name:
            return None
        project = projects.get(project_name, {})
        return project.get("path")

    return None


def _resolve_ref_step_field(ref_step_param: str, step: Step,
                            context: Dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[Any]]:
    """
    For FILE_UNCHANGED and NOT_EMPTY: resolves "input.<key>" to the raw
    reference string (e.g. "$s1.content"), then extracts step_id and field,
    and retrieves the value from context.

    Returns (step_id, field_name, value) — any can be None if unresolvable.
    """
    if not ref_step_param.startswith("input."):
        return None, None, None

    key = ref_step_param[len("input."):]
    raw = step.input.get(key)
    if not isinstance(raw, str):
        return None, None, None

    match = _REF_PATTERN.match(raw.strip())
    if not match:
        return None, None, None

    step_id: str = match.group(1)
    field_name: str = match.group(2)
    value = context.get(step_id, {}).get(field_name)
    return step_id, field_name, value


def _is_filesystem_read_step(step_id: str, plan: Plan) -> bool:
    """
    Returns True if the step with that id is a filesystem.read step.
    FILE_UNCHANGED is only meaningful when the source step read from disk.
    """
    for s in plan.steps:
        if s.id == step_id:
            return s.tool_or_worker == "filesystem" and s.action == "read"
    return False


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Operator evaluators
# ---------------------------------------------------------------------------

def _eval_file_exists(assume: Dict, step: Step, plan: Plan,
                      context: Dict[str, Any], projects: Dict[str, Any]) -> Optional[AssumeFailure]:
    path_str = _resolve_param(assume.get("path", ""), step, context, projects)
    if path_str is None:
        return AssumeFailure(
            op="FILE_EXISTS",
            reason=f"Could not resolve path parameter '{assume.get('path')}' for step '{step.id}'.",
            assume=assume,
        )
    p = Path(path_str).expanduser()
    # If relative, try to resolve against the project path
    if not p.is_absolute():
        project_name = step.input.get("project")
        project_path = projects.get(project_name, {}).get(
            "path") if project_name else None
        if project_path:
            p = Path(project_path).expanduser() / p
    if not p.exists() or not p.is_file():
        return AssumeFailure(
            op="FILE_EXISTS",
            reason=f"File does not exist: {p}",
            assume=assume,
        )
    return None


def _eval_file_exists_if_present(assume: Dict, step: Step, plan: Plan,
                                 context: Dict[str, Any], projects: Dict[str, Any]) -> Optional[AssumeFailure]:
    """
    Conditional FILE_EXISTS — only evaluates if the input key is present
    in the step's input dict. Used for optional inputs like worker_ts_check's file_path.
    """
    param = assume.get("path", "")
    if param.startswith("input."):
        key = param[len("input."):]
        if key not in step.input:
            # Key not present — assume is not applicable, skip silently
            return None
    return _eval_file_exists(assume, step, plan, context, projects)


def _eval_dir_exists(assume: Dict, step: Step, plan: Plan,
                     context: Dict[str, Any], projects: Dict[str, Any]) -> Optional[AssumeFailure]:
    path_str = _resolve_param(assume.get("path", ""), step, context, projects)
    if path_str is None:
        return AssumeFailure(
            op="DIR_EXISTS",
            reason=f"Could not resolve path parameter '{assume.get('path')}' for step '{step.id}'.",
            assume=assume,
        )
    # For filesystem.write: we check the parent directory, not the file itself
    p = Path(path_str).expanduser()
    target = p.parent if p.suffix else p  # heuristic: has extension → it's a file
    if not target.exists() or not target.is_dir():
        return AssumeFailure(
            op="DIR_EXISTS",
            reason=f"Directory does not exist: {target}",
            assume=assume,
        )
    return None


def _eval_file_unchanged(assume: Dict, step: Step, plan: Plan,
                         context: Dict[str, Any], projects: Dict[str, Any]) -> Optional[AssumeFailure]:
    """
    Verifies that the file on disk hasn't changed since the source step read it.

    Chain:
      assume.ref_step = "input.file_content"
        → step.input["file_content"] = "$s1.content"
          → source step_id = "s1"
          → context["s1"]["content"] = text read at T0
          → plan.steps["s1"].input["path"] = path on disk
        → hash(disk) == hash(context content)

    Skipped silently if the source step is not a filesystem.read
    (content came from another worker, never existed on disk).
    """
    ref_step_param = assume.get("ref_step", "")
    source_step_id, field_name, content_at_t0 = _resolve_ref_step_field(
        ref_step_param, step, context
    )

    if source_step_id is None:
        # Can't resolve the reference — can't evaluate, skip silently
        # (better than a false positive that blocks a valid plan)
        return None

    # Only meaningful if the source step read from disk
    if not _is_filesystem_read_step(source_step_id, plan):
        return None

    if content_at_t0 is None:
        return AssumeFailure(
            op="FILE_UNCHANGED",
            reason=f"Could not retrieve content from step '{source_step_id}' context.",
            assume=assume,
        )

    # Get the path the source step read from
    source_step = next((s for s in plan.steps if s.id == source_step_id), None)
    if source_step is None:
        return None

    file_path = source_step.input.get("path")
    if not file_path:
        return None

    p = Path(file_path).expanduser()
    if not p.exists():
        return AssumeFailure(
            op="FILE_UNCHANGED",
            reason=f"File no longer exists on disk: {file_path}",
            assume=assume,
        )

    current_content = p.read_text(encoding="utf-8")
    if _hash(current_content) != _hash(str(content_at_t0)):
        return AssumeFailure(
            op="FILE_UNCHANGED",
            reason=(
                f"File '{file_path}' has been modified since step '{source_step_id}' read it. "
                f"The plan was generated against a version that no longer matches disk."
            ),
            assume=assume,
        )
    return None


def _eval_command_exists(assume: Dict, step: Step, plan: Plan,
                         context: Dict[str, Any], projects: Dict[str, Any]) -> Optional[AssumeFailure]:
    command_str = _resolve_param(assume.get(
        "command", ""), step, context, projects)
    if command_str is None:
        return AssumeFailure(
            op="COMMAND_EXISTS",
            reason=f"Could not resolve command parameter for step '{step.id}'.",
            assume=assume,
        )
    if shutil.which(command_str) is None:
        return AssumeFailure(
            op="COMMAND_EXISTS",
            reason=f"Command not found in PATH: '{command_str}'",
            assume=assume,
        )
    return None


def _eval_service_reachable(assume: Dict, step: Step, plan: Plan,
                            context: Dict[str, Any], projects: Dict[str, Any]) -> Optional[AssumeFailure]:
    host = assume.get("host", "localhost")
    port = assume.get("port", 3333)
    url = f"http://{host}:{port}/health"
    try:
        resp = requests.get(url, timeout=2)
        if resp.status_code != 200:
            return AssumeFailure(
                op="SERVICE_REACHABLE",
                reason=f"Service at {url} returned HTTP {resp.status_code}.",
                assume=assume,
            )
    except requests.exceptions.ConnectionError:
        return AssumeFailure(
            op="SERVICE_REACHABLE",
            reason=f"Service at {url} is not reachable. Is the VSCode extension running?",
            assume=assume,
        )
    except requests.exceptions.Timeout:
        return AssumeFailure(
            op="SERVICE_REACHABLE",
            reason=f"Service at {url} timed out after 2s.",
            assume=assume,
        )
    return None


def _eval_is_git_repo(assume: Dict, step: Step, plan: Plan,
                      context: Dict[str, Any], projects: Dict[str, Any]) -> Optional[AssumeFailure]:
    path_str = _resolve_param(assume.get("path", ""), step, context, projects)
    if path_str is None:
        return AssumeFailure(
            op="IS_GIT_REPO",
            reason=f"Could not resolve path parameter for step '{step.id}'.",
            assume=assume,
        )
    git_dir = Path(path_str).expanduser() / ".git"
    if not git_dir.exists():
        return AssumeFailure(
            op="IS_GIT_REPO",
            reason=f"No .git directory found at: {path_str}",
            assume=assume,
        )
    return None


def _eval_not_empty(assume: Dict, step: Step, plan: Plan,
                    context: Dict[str, Any], projects: Dict[str, Any]) -> Optional[AssumeFailure]:
    """
    Verifies that the referenced value is a non-empty string.
    ref_field in the registry is informational only — the field name
    is already encoded in the reference string (e.g. "$s1.stdout").
    """
    ref_step_param = assume.get("ref_step", "")
    source_step_id, field_name, value = _resolve_ref_step_field(
        ref_step_param, step, context
    )

    if source_step_id is None or value is None:
        # Can't resolve — skip silently, the Director will catch a missing
        # input at dispatch time anyway
        return None

    if not str(value).strip():
        return AssumeFailure(
            op="NOT_EMPTY",
            reason=(
                f"Value from step '{source_step_id}'.{field_name} is empty. "
                f"Nothing to process."
            ),
            assume=assume,
        )
    return None


# ---------------------------------------------------------------------------
# Operator dispatch
# ---------------------------------------------------------------------------

_EVALUATORS = {
    "FILE_EXISTS":          _eval_file_exists,
    "FILE_EXISTS_IF_PRESENT": _eval_file_exists_if_present,
    "DIR_EXISTS":           _eval_dir_exists,
    "FILE_UNCHANGED":       _eval_file_unchanged,
    "COMMAND_EXISTS":       _eval_command_exists,
    "SERVICE_REACHABLE":    _eval_service_reachable,
    "IS_GIT_REPO":          _eval_is_git_repo,
    "NOT_EMPTY":            _eval_not_empty,
}


# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------

def _get_implicit_assumes(step: Step, registry: Dict) -> List[Dict]:
    """
    Retrieves the implicit_assumes list for this step from the loaded registry.

    For tool steps (filesystem, terminal, git, vscode): looks up
    tools[name].actions[action].implicit_assumes.

    For worker steps (worker_*): looks up workers[name].implicit_assumes.
    """
    tool_name = step.tool_or_worker

    if not tool_name.startswith("worker_"):
        # Primitive tool — find the matching action entry
        for tool in registry.get("tools", []):
            if tool["name"] == tool_name:
                for action in tool.get("actions", []):
                    if action["name"] == step.action:
                        return action.get("implicit_assumes", [])
        return []

    # Worker
    for worker in registry.get("workers", []):
        if worker["name"] == tool_name:
            return worker.get("implicit_assumes", [])
    return []


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def check(step: Step, plan: Plan, context: Dict[str, Any],
          registry: Dict, projects: Optional[Dict] = None) -> ComparatorResult:
    """
    Evaluates all implicit_assumes for the given step.

    Called by DirectorInstance immediately before dispatching a step.
    Returns ComparatorResult with passed=True if all assumes hold,
    or passed=False with a list of AssumeFailure describing what failed.

    Never raises — any internal error is caught and surfaced as a failure
    so the Director always gets a clean result to act on.
    """
    assumes = _get_implicit_assumes(step, registry)
    if not assumes:
        return ComparatorResult(passed=True)

    failures: List[AssumeFailure] = []
    resolved_projects: Dict = projects or {}

    for assume in assumes:
        op: str = assume.get("op") or ""
        if not op:
            failures.append(AssumeFailure(
                op="UNKNOWN",
                reason="Assume entry in registry is missing the 'op' field. This is a registry bug.",
                assume=assume,
            ))
            continue

        evaluator = _EVALUATORS.get(op)
        if evaluator is None:
            # Unknown op — fail safe: report it so it doesn't silently pass
            failures.append(AssumeFailure(
                op=op,
                reason=f"Unknown assume operator '{op}' in registry. This is a registry bug.",
                assume=assume,
            ))
            continue

        try:
            failure = evaluator(assume, step, plan, context, resolved_projects)
            if failure is not None:
                failures.append(failure)
        except Exception as e:
            failures.append(AssumeFailure(
                op=op,
                reason=f"Comparator error while evaluating '{op}': {e}",
                assume=assume,
            ))

    return ComparatorResult(passed=len(failures) == 0, failed_assumes=failures)

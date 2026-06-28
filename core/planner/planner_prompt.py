"""
System prompt construction for the Planner (Kimi K2.6 via NIM).

Pulled out of planner.py into its own module so the prompt can be
iterated on quickly without touching the HTTP/parsing logic that
consumes it. planner.py imports build_system_prompt() from here and
treats it as an opaque string -- nothing in planner.py should know or
care about the prompt's internal structure.
"""

from typing import List

import yaml

from registry.tool_registry import TOOLS_BY_NAME
from registry.worker_registry import WORKERS_BY_NAME

_PROJECT_REGISTRY_PATH = "registry/project_registry.yaml"


def _build_registry_context() -> str:
    """
    Serializes the full Tool + Worker catalogs into a plain-text block
    for the system prompt. Kimi reads this to know what Nova can do and
    to pick exact, valid names for Step.tool_or_worker -- this is what
    makes validate_plan_against_registry's job possible in the first
    place: Kimi is never guessing a name nobody gave it.
    """
    lines: List[str] = ["AVAILABLE TOOLS (primitive, no LLM):"]
    for name, entry in TOOLS_BY_NAME.items():
        lines.append(f"  - {name}: {entry.get('description', '')}")

    lines.append("")
    lines.append("AVAILABLE WORKERS (LLM-powered, one job each):")
    for name, entry in WORKERS_BY_NAME.items():
        lines.append(f"  - {name}: {entry.get('description', '')}")

    return "\n".join(lines)


def _build_project_context() -> str:
    """
    Loads project_registry.yaml verbatim as text context. This is
    what the "project" rule below points back to -- Kimi must copy a
    name EXACTLY from this list into any worker_* step's input, never
    invent or rephrase one. See
    core/planner/validators.py::validate_step_projects_exist /
    core/domain/exceptions/contract_errors.py::InvalidProjectError for
    the contract check that catches it if this rule is violated
    anyway.
    """
    with open(_PROJECT_REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    lines = ["KNOWN PROJECTS:"]
    for project_name, info in data.items():
        lines.append(
            f"  - {project_name}: path={info.get('path')}, "
            f"lang={info.get('lang')}, run={info.get('run')}, test={info.get('test')}"
        )
    return "\n".join(lines)


# Single source of truth for "what actually has a real dispatch
# function today", separate from the Tool/Worker Registry (which lists
# everything that EXISTS as a catalog entry, regardless of whether it
# has a real implementation behind it yet). The registry alone is not
# enough to keep Kimi from picking a real, valid, registered worker
# name that nonetheless has no dispatch function in
# core/director/director_instance.py's _TOOL_DISPATCH yet -- that would
# pass contract validation (the name IS registered) but fail at
# execution with a confusing error, instead of Kimi simply not
# proposing it in the first place.
#
# Update this list as each entry gets a real implementation -- when
# core/director/director_instance.py's _TOOL_DISPATCH grows a new
# entry, remove the matching line here. The two lists should always
# describe the same reality; this one is what tells Kimi about it.
IMPLEMENTED_TOOLS_AND_WORKERS = [
    "filesystem (actions: read, write, list)",
    "terminal (action: run)",
    "worker_ts_check",
    "worker_ts_fix",
    "worker_jsdoc",
    "worker_test_writer",
    "worker_commit_msg",
    "worker_diff_summary",
]


def _build_implementation_status_context() -> str:
    lines = [
        "CURRENTLY IMPLEMENTED (only these have a real dispatch behind "
        "them -- do not propose any other tool_or_worker, even if it "
        "appears in the catalog above, since it would pass validation "
        "but fail when the Director actually tries to run it):",
    ]
    for entry in IMPLEMENTED_TOOLS_AND_WORKERS:
        lines.append(f"  - {entry}")
    return "\n".join(lines)


SYSTEM_PROMPT_TEMPLATE = """You are Nova's Planner. You convert a natural language task into a \
structured execution plan, or ask clarifying questions if the task is \
ambiguous.

{registry_context}

{implementation_status_context}

{project_context}

RESPONSE CONTRACT — you must respond with ONLY valid JSON, no prose \
before or after, no markdown code fences. Your response must be exactly \
one of these two shapes:

If the task is ambiguous and you need more information before planning:
{{"status": "clarification_needed", "questions": ["...", "..."], "plan": null}}

If you can plan the task as given:
{{"status": "ready", "questions": [], "plan": {{"objective": "...", "steps": [{{"id": "...", "description": "...", "tool_or_worker": "...", "action": "...", "depends_on": ["..."], "input": {{}}, "assumes": ["..."]}}]}}}}

Rules:
- "tool_or_worker" must be EXACTLY one of the names listed above. Never \
invent a name.
- Even if a tool_or_worker name appears in the catalog above, it can \
ONLY be used in a plan if it also appears in the "CURRENTLY \
IMPLEMENTED" list. A registered-but-not-yet-implemented name will fail \
when the Director tries to execute it -- if the task needs something \
not in that list, ask a clarification question explaining what's \
missing instead of proposing a plan that would fail.
- Worker names always start with "worker_". Tool names never do.
- "action" is REQUIRED when "tool_or_worker" is a primitive tool with \
multiple operations: for "filesystem", use "read", "write", or "list". \
For "terminal", use "run". Workers do not need "action" -- leave it as \
an empty string "" for any worker_* step, since the worker name itself \
already specifies the one thing it does.
- For "terminal" steps, "input" MUST have a "command" key (the base \
executable, e.g. "cat", "git", "npm" -- never combined with arguments \
into one string) and an "args" key (a list of separate argument \
strings). Example: {{"command": "cat", "args": ["$s1.path"]}}, NOT \
{{"command": "cat $s1.path"}} -- the latter will never resolve, since \
a "$step_id.field" reference must be an entire argument by itself, \
never embedded inside a longer string alongside other text.
- "depends_on" lists the "id" of every step that MUST complete before \
this one can start. Use an empty list [] if this step has no \
prerequisites and can run immediately. Steps with no shared dependency \
chain between them (e.g. two independent file reads) should have \
INDEPENDENT depends_on lists so they can run in parallel -- do not \
force unrelated steps into an artificial sequence.
- "input" is a dict of concrete parameters for this step's tool or \
worker (e.g. {{"path": "signal.ts"}} for a filesystem read). To use the \
result of an earlier step as a parameter, set the value to the exact \
string "$<step_id>.<field>" (e.g. "$s1.content") -- this is resolved \
to the real value before execution, you do not need to know what the \
real value will be. Only reference a step_id that is listed in this \
step's own "depends_on" -- referencing a step you don't depend on \
means its result may not exist yet when this step runs.
- Every worker_* step's "input" MUST include a "project" key whose \
value is EXACTLY one of the names listed under "KNOWN PROJECTS" below \
-- never invent a project name, never abbreviate or rephrase one. This \
is how a worker knows which codebase it's operating on. A worker_* \
step with no "project" key, or one that doesn't match a known project \
name exactly, will fail contract validation before it ever runs.
- List "steps" in an order where every step's dependencies appear \
EARLIER in the list than the step itself. This must hold even though \
step ordering and execution are conceptually separate concerns.
- "assumes" lists the concrete assumptions this step depends on (e.g. \
which file, which branch) -- this is required for every step, even if \
the list is empty.
- Ask at most 3 questions if clarification is needed.
"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        registry_context=_build_registry_context(),
        implementation_status_context=_build_implementation_status_context(),
        project_context=_build_project_context(),
    )

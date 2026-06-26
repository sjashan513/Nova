"""
Domain models — shared data contracts.

These are the "interfaces" of the system: pure data shape, no behavior.
Anything that needs to know what a Step or a Plan looks like imports
from here. This module must never import from planner/, director/,
or any other layer — domain is the bottom of the dependency graph.
"""

from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field


class Step(BaseModel):
    id: str
    description: str

    # v1 deliberately keeps this as a single free-form string instead of
    # splitting into `tool: Optional[str]` / `worker: Optional[str]`.
    # The type (tool vs. worker) is resolved by convention: anything
    # prefixed with "worker_" is treated as a worker, everything else
    # is treated as a primitive tool. See:
    # core/planner/validators.py::validate_plan_against_registry
    tool_or_worker: str

    # Fase 2 addition (discovered missing while building director_instance.py):
    # nova_technical_overview.md's original Step shape already defined
    # this ("read" | "worker_jsdoc" | "commit" | "show_diff" | ...) but
    # it never made it into this schema. Needed because tool_or_worker
    # alone is not specific enough to dispatch -- "filesystem" names the
    # TOOL, but read/write/list are three different operations on that
    # same tool. Default "" (not required) because LLM-powered workers
    # (single-responsibility by design) don't need this distinction the
    # same way a primitive tool with multiple operations does -- a
    # worker_ts_fix step IS the action, it has nothing else to specify.
    # Primitive multi-operation tools (filesystem, terminal) DO require
    # a non-empty action -- core/director/director_instance.py's
    # dispatch table raises if it's missing for those.
    action: str = ""

    # Fase 2 addition: explicit dependency declaration. Steps with no
    # entries here (default []) have no prerequisites and belong to
    # execution level 0. This is what makes real parallelism possible
    # at all -- without it, the only thing the Director could infer is
    # "step N comes after step N-1" from array position, which would
    # make every plan strictly sequential and defeat the entire point
    # of Fase 2 (see NOVA_CLI_MVP_ROADMAP.md Fase 2, "parallel reads").
    # Values reference other Step.id strings within the SAME plan.
    # Validity (every referenced id exists, no cycles) is NOT checked
    # by this model -- that's core/director/dag.py::validate_dag's job,
    # invoked by the Director before any execution is attempted.
    depends_on: List[str] = Field(default_factory=list)

    # Fase 2 addition: structured parameters for this step's tool or
    # worker call. Values may be the literal string "$<step_id>.<field>"
    # to reference another step's `result` dict within the SAME plan --
    # resolved by core/director/context.py before dispatch, never by
    # the worker/tool itself (workers stay stateless, per the original
    # architecture's "intelligence vs. execution" split).
    input: Dict[str, Any] = Field(default_factory=dict)

    status: Literal["pending", "in_progress",
                    "completed", "failed"] = "pending"
    assumes: List[str] = Field(default_factory=list)

    # Changed from Optional[str] to Optional[Dict] in Fase 2: a plain
    # string result has no "field" for $step_id.field to point at.
    # A structured dict is what makes the .field part of that syntax
    # mean anything at all.
    result: Optional[Dict[str, Any]] = None


class Plan(BaseModel):
    objective: str
    steps: List[Step]

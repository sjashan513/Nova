"""
Domain models — shared data contracts.

These are the "interfaces" of the system: pure data shape, no behavior.
Anything that needs to know what a Step or a Plan looks like imports
from here. This module must never import from planner/, director/,
or any other layer — domain is the bottom of the dependency graph.
"""

from typing import List, Optional, Literal
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

    status: Literal["pending", "in_progress",
                    "completed", "failed"] = "pending"
    assumes: List[str] = Field(default_factory=list)
    result: Optional[str] = None


class Plan(BaseModel):
    objective: str
    steps: List[Step]

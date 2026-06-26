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
    tool_or_worker: str
    action: str = ""
    depends_on: List[str] = Field(default_factory=list)
    input: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "in_progress",
                    "completed", "failed"] = "pending"
    assumes: List[str] = Field(default_factory=list)

    result: Optional[Dict[str, Any]] = None

    model: Optional[str] = None
    fallback_model: Optional[str] = None


class Plan(BaseModel):
    objective: str
    steps: List[Step]

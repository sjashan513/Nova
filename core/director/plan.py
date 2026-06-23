from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class Step(BaseModel):
    id: str
    description: str
    tool_or_worker: str
    status: Literal["pending", "in_progress",
                    "completed", "failed"] = "pending"
    assumes: List[str] = Field(default_factory=list)
    result: Optional[str] = None


class Plan(BaseModel):
    objective: str
    steps: List[Step]

"""
Plan contract validation.

Owned exclusively by the Iniciador. This is the gate between "Kimi
returned a JSON plan" and "this plan is allowed to reach the Router".
A plan that fails this check never acquires a lock, never spawns a
Director instance -- it dies here, in the Iniciador's hands, the same
place that already owns the clarification loop and the decision of
when a plan is `ready`.

This module is intentionally separate from core/director/. The day the
Director needs its own validation logic (e.g. something related to
divergence in Fase 5), it gets its own validators.py living next to it.
Shared data shape (Step, Plan, the exceptions) lives in core/domain/ and
is imported by both -- this module owns the *rule*, not the *shape*.

Fase 1 change (see NOVA_PLANNER_LAYER_ADR.md §2.1): this used to raise
on the first invalid Step. It now walks the entire plan and returns a
list of every PlanContractError found. This is what makes the
Iniciador's bounded retry loop (get_plan(), core/planner/iniciador.py)
actually converge: a single round-trip to Kimi can carry every real
error in the plan, instead of spending one retry attempt per error
discovered one at a time.
"""

from typing import List, Optional

from core.domain.models import Plan
from core.domain.exceptions import PlanContractError, WorkerNotFoundError, ToolNotFoundError
from registry.tool_registry import tool_exists, list_tool_names
from registry.worker_registry import worker_exists, list_worker_names

_WORKER_PREFIX = "worker_"


def validate_plan_against_registry(
    plan: Plan, plan_id: Optional[str] = None
) -> List[PlanContractError]:
    """
    Checks every Step in `plan` against the tool and worker registries.

    Type resolution convention (sealed for v1): a Step's `tool_or_worker`
    is treated as a worker if it starts with "worker_", otherwise it is
    treated as a primitive tool. This is a naming convention, not a typed
    field -- Step.tool_or_worker stays a single str (see domain/models.py).

    Does NOT raise. Every step is checked, even after an earlier one
    fails -- the caller (the Iniciador's retry loop) needs the complete
    picture in one pass, not just the first problem found.

    Returns:
        A list of PlanContractError instances, one per invalid step,
        in plan order. An empty list means the plan is contractually
        valid -- every step references something that actually exists
        in the registry. This says nothing about whether the plan is
        semantically sound; that is out of scope for this check.
    """
    errors: List[PlanContractError] = []

    for step in plan.steps:
        ref = step.tool_or_worker

        if ref.startswith(_WORKER_PREFIX):
            if not worker_exists(ref):
                errors.append(
                    WorkerNotFoundError(
                        plan_id=plan_id,
                        step_id=step.id,
                        raw_value=ref,
                        available_workers=list_worker_names(),
                    )
                )
        else:
            if not tool_exists(ref):
                errors.append(
                    ToolNotFoundError(
                        plan_id=plan_id,
                        step_id=step.id,
                        raw_value=ref,
                        available_tools=list_tool_names(),
                    )
                )

    return errors

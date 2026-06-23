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
"""

from typing import Optional

from core.domain.models import Plan
from core.domain.exceptions import WorkerNotFoundError, ToolNotFoundError
from registry.tool_registry import tool_exists, list_tool_names
from registry.worker_registry import worker_exists, list_worker_names

_WORKER_PREFIX = "worker_"


def validate_plan_against_registry(plan: Plan, plan_id: Optional[str] = None) -> None:
    """
    Checks every Step in `plan` against the tool and worker registries.

    Type resolution convention (sealed for v1): a Step's `tool_or_worker`
    is treated as a worker if it starts with "worker_", otherwise it is
    treated as a primitive tool. This is a naming convention, not a typed
    field -- Step.tool_or_worker stays a single str (see domain/models.py).

    Raises:
        WorkerNotFoundError: tool_or_worker matched the worker naming
            convention but no such worker is registered.
        ToolNotFoundError: tool_or_worker did not match the worker naming
            convention and no such tool is registered.

    Returns:
        None. Silence means the plan is contractually valid -- every
        step references something that actually exists in the registry.
        This says nothing about whether the plan is semantically sound;
        that is out of scope for this check.
    """
    for step in plan.steps:
        ref = step.tool_or_worker

        if ref.startswith(_WORKER_PREFIX):
            if not worker_exists(ref):
                raise WorkerNotFoundError(
                    plan_id=plan_id,
                    step_id=step.id,
                    raw_value=ref,
                    available_workers=list_worker_names(),
                )
        else:
            if not tool_exists(ref):
                raise ToolNotFoundError(
                    plan_id=plan_id,
                    step_id=step.id,
                    raw_value=ref,
                    available_tools=list_tool_names(),
                )

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

Fase 3 addition (see NOVA_WORKER_LAYER_ADR.md §2.1 / session notes): a
second, independent check -- validate_worker_steps_have_model -- plus
a thin orchestrator, validate_plan_contract, that runs every check and
concatenates their results. Kept as separate functions rather than one
fused loop deliberately: this is the same "different question over the
same array" situation as core/director/dag.py's validate_dag vs.
validate_input_references -- "does this name exist in the registry?"
and "does this worker have a model set?" are independent rules that
happen to both scan plan.steps, not one rule split into two for no
reason. The Iniciador only ever needs to call validate_plan_contract;
the individual checks stay importable and testable on their own.
"""

from typing import List, Optional

from core.domain.models import Plan
from core.domain.exceptions import (
    PlanContractError,
    WorkerNotFoundError,
    ToolNotFoundError,
    MissingModelError,
)
from registry.tool_registry import tool_exists, list_tool_names
from registry.worker_registry import (
    worker_exists,
    list_worker_names,
    worker_requires_model,
)

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


def validate_worker_steps_have_model(
    plan: Plan, plan_id: Optional[str] = None
) -> List[PlanContractError]:
    """
    Checks every worker Step that requires an LLM (requires_model: true
    in registry/tool_registry.yaml for that worker) has Step.model set.

    Deliberately skips, rather than flags, three cases:
      - tool_or_worker is not a worker at all (primitive tool) -- not
        this function's concern.
      - tool_or_worker IS shaped like a worker name but does not exist
        in the registry -- that is validate_plan_against_registry's
        job (WorkerNotFoundError). Re-flagging it here too would
        produce two errors for the same root cause and clutter the
        retry context built for Kimi with redundant noise.
      - tool_or_worker exists and requires_model is false (e.g.
        worker_ts_check) -- nothing to check; that worker has no
        concept of a model at all.

    Does NOT check Step.fallback_model -- that field is optional by
    design (see NOVA_WORKER_LAYER_ADR.md §7: nova_technical_overview.md
    describes it as "if primary fails"). A Step with no fallback_model
    simply has no Level 2 fallback available and escalates straight to
    Level 3 if its primary model call fails -- expected behavior, not
    a contract violation.

    Returns a list of errors (empty = valid), same pattern as every
    other contract check in this codebase.
    """
    errors: List[PlanContractError] = []

    for step in plan.steps:
        ref = step.tool_or_worker

        if not ref.startswith(_WORKER_PREFIX):
            continue
        if not worker_exists(ref):
            continue
        if not worker_requires_model(ref):
            continue
        if step.model is None:
            errors.append(
                MissingModelError(
                    plan_id=plan_id,
                    step_id=step.id,
                    raw_value=ref,
                )
            )

    return errors


def validate_plan_contract(
    plan: Plan, plan_id: Optional[str] = None
) -> List[PlanContractError]:
    """
    Single entry point the Iniciador calls. Runs every individual
    contract check and concatenates their results into one list, so a
    single round-trip to Kimi can carry every real problem at once --
    same reasoning as why each individual check returns a list instead
    of raising on the first error (NOVA_PLANNER_LAYER_ADR.md §2.1).

    Each underlying check stays its own independent, testable function
    -- this is purely a composition point, same pattern as
    core/director/director_instance.py's run() calling validate_dag
    and validate_input_references in sequence rather than fusing them
    into one loop.

    Add new contract checks here as they're built -- callers (the
    Iniciador) only ever need to know about this one function.
    """
    return (
        validate_plan_against_registry(plan, plan_id)
        + validate_worker_steps_have_model(plan, plan_id)
    )

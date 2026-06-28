"""
Iniciador — single entry point for every task in Nova.

This is the only component that talks to the Planner (planner.call) and
owns the decision of when a plan is `ready` enough to leave this layer.
The Router never sees a plan that failed here -- by design, no lock is
ever acquired for a plan that this module rejects.

Two distinct, NOT merged, retry concerns live in this module:

  1. Clarification loop (status == "clarification_needed") -- still
     uncapped, see NOVA_DIRECTOR_LAYER_ADR.md §7.1 (open debt, not
     addressed here).
  2. Contract retry loop (PlannerError / PlanContractError) -- capped at
     MAX_CONTRACT_RETRIES, fully designed and implemented here. See
     NOVA_PLANNER_LAYER_ADR.md §2.4 for why these two loops are kept
     separate rather than merged into one generic "Kimi failed, retry"
     mechanism.

Sealed design decisions this module implements (see the ADR for the
full rationale, not repeated here):
  - validate_plan_contract (core/planner/validators.py) returns a LIST
    of errors so a single retry round-trip can address every real
    problem in the plan at once, instead of spending one attempt per
    error discovered. As of Fase 3 this is an orchestrator over
    multiple individual checks (registry existence, worker model
    completeness) -- see NOVA_WORKER_LAYER_ADR.md / session notes;
    this module only ever calls the one orchestrator function, it
    does not know or care how many checks live behind it.
  - PlannerError and PlanContractError SHARE one retry counter
    (explicit v1 simplification -- see ADR §7, open debt #1).
  - Retry context is built from the concrete attributes of whichever
    errors actually fired (available_workers / available_tools /
    the worker name for a missing model) -- Option A from the design
    session: one call to Kimi carrying full context, never split into
    per-error-type "personality" calls.
  - On exhaustion with unresolved PlanContractErrors, raises
    PlanContractErrorGroup -- never silently drops any of them.
"""

from typing import Any, Dict, List, Optional

from core.domain.exceptions import (
    PlannerError,
    PlanContractError,
    PlanContractErrorGroup,
    WorkerNotFoundError,
    ToolNotFoundError,
    MissingModelError,
    InvalidProjectError,
)
from core.planner import planner
from core.planner.validators import validate_plan_contract

MAX_CONTRACT_RETRIES = 2


def _build_retry_context(errors: List[PlanContractError]) -> str:
    """
    Turns a list of PlanContractErrors into a single plain-text block
    Kimi can act on. Each error contributes its own specific detail
    (available_workers / available_tools / the worker name needing a
    model) -- this is what Option A means in practice: no separate
    "personality" calls per error type, just one message carrying
    everything that's wrong.
    """
    lines = [
        "Your previous plan had one or more contract problems. Fix ALL "
        "of the following in your next response:"
    ]
    for err in errors:
        if isinstance(err, WorkerNotFoundError):
            lines.append(
                f"  - Step '{err.step_id}': '{err.raw_value}' is not a "
                f"registered worker. Valid workers: "
                f"{', '.join(err.available_workers)}"
            )
        elif isinstance(err, ToolNotFoundError):
            lines.append(
                f"  - Step '{err.step_id}': '{err.raw_value}' is not a "
                f"registered tool. Valid tools: "
                f"{', '.join(err.available_tools)}"
            )
        elif isinstance(err, MissingModelError):
            lines.append(
                f"  - Step '{err.step_id}': worker '{err.raw_value}' "
                f"requires a model -- set \"model\" to a valid model "
                f"string for this step."
            )
        elif isinstance(err, InvalidProjectError):
            lines.append(
                f"  - Step '{err.step_id}': '{err.raw_value}' is not a "
                f"registered project. Valid projects: "
                f"{', '.join(err.available_projects)}"
            )
        else:
            # Defensive fallback -- should not happen with today's
            # known subtypes, but if a future PlanContractError subtype
            # is added without one of these explicit branches, fail
            # loud in the prompt rather than silently producing a
            # useless message.
            lines.append(f"  - Step '{err.step_id}': {err}")

    return "\n".join(lines)


def _build_planner_error_context(error: PlannerError) -> str:
    """
    Retry context for the PlannerError case (bad JSON / bad shape).
    Less specific than the contract case -- we don't have a list of
    valid names to offer, just a signal that the format was wrong.
    """
    return (
        "Your previous response could not be parsed. You MUST respond "
        "with ONLY valid JSON matching the exact contract described "
        f"above, no prose, no markdown fences. Error was: {error}"
    )


class Iniciador:
    """
    Single entry point for converting a natural language task into a
    validated, ready-to-execute Plan. One instance is enough for the
    whole CLI process in v1 -- this is not per-task state, it's a thin
    owner of the retry loop and the call to the Planner.
    """

    def __init__(self, max_contract_retries: int = MAX_CONTRACT_RETRIES):
        self.max_contract_retries = max_contract_retries

    def get_plan(self, task: str) -> Dict[str, Any]:
        """
        Returns a dict with the same shape planner.call() returns:
        {"status": "clarification_needed", "questions": [...], "plan": None}
        or
        {"status": "ready", "questions": [], "plan": <Plan>}

        Raises:
            PlanContractErrorGroup: contract retries exhausted with one
                or more unresolved PlanContractErrors (any subtype --
                registry mismatch, missing model, or future checks
                added to validate_plan_contract).
            PlannerError: a PlannerError (or subtype) fired on the final
                allowed attempt and was not recovered from. Propagated
                as-is -- it is not wrapped into PlanContractErrorGroup,
                since that group is specifically for PlanContractError,
                not for PlannerError (see module docstring on why these
                two families are kept separate).
        """
        attempts = 0
        retry_context: Optional[str] = None
        last_planner_error: Optional[PlannerError] = None

        while attempts < self.max_contract_retries:
            try:
                response = planner.call(task, retry_context=retry_context)
            except PlannerError as e:
                attempts += 1
                last_planner_error = e
                if attempts >= self.max_contract_retries:
                    raise
                retry_context = _build_planner_error_context(e)
                continue

            if response["status"] == "clarification_needed":
                # Clarification loop is a separate concern (still
                # uncapped, ADR Director §7.1) -- not part of this
                # retry budget at all.
                return response

            # status == "ready"
            attempts += 1
            errors = validate_plan_contract(response["plan"])

            if not errors:
                return response

            if attempts >= self.max_contract_retries:
                raise PlanContractErrorGroup(plan_id=None, errors=errors)

            retry_context = _build_retry_context(errors)

        # Defensive: the loop above always returns or raises before
        # falling through, but if max_contract_retries were ever 0 this
        # guards against silently returning nothing.
        if last_planner_error is not None:
            raise last_planner_error
        raise RuntimeError(
            "Iniciador.get_plan exhausted retries without a final "
            "result or error -- this should be unreachable."
        )

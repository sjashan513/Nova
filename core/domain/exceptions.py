"""
Domain exceptions — the small, closed vocabulary of things that can go
wrong in Nova.

Design decision (sealed in the Fase 0 design session, June 2026):

    NovaError                      # root, never raised directly
      |
      +-- PlanContractError        # plan references something that does
      |     |                      # not exist in the registry — caught
      |     |                      # by the Iniciador BEFORE the plan
      |     |                      # ever reaches the Router/Director
      |     |
      |     +-- WorkerNotFoundError
      |     +-- ToolNotFoundError
      |
      +-- ExecutionError           # RESERVED — Fase 3+. Director/Worker
      |                            # runtime failures (timeouts, bad
      |                            # output shape, retries exhausted).
      |                            # Empty on purpose, do not populate yet.
      |
      +-- DivergenceError          # RESERVED — Fase 5. Raised when the
                                   # divergence comparator finds a worker's
                                   # reported `assumes` does not match what
                                   # Kimi declared, and is then judged by
                                   # the Reviewer. Empty on purpose.

Why one root with families instead of a flat list:
A consumer (Iniciador today, Director/Gemma later) can catch the family
(`except PlanContractError`) without enumerating every subtype, while
the log and Jashan still see the exact subtype that fired. This is the
same shape the Reviewer's `benign | fatal | ambiguous` verdict will
eventually map onto — we are not building that mapping yet, just making
sure the vocabulary won't have to be reshuffled when we do.

Ownership: PlanContractError and its children are raised and handled
exclusively by the Iniciador (core/planner/validators.py). The Director
never sees a plan that failed this check — by design, a plan that fails
contract validation never reaches the Router.
"""

from typing import List, Optional


class NovaError(Exception):
    """Root of all domain-specific errors in Nova. Never raised directly."""


# ---------------------------------------------------------------------------
# PlanContractError family — Fase 0. Owned by the Iniciador.
# ---------------------------------------------------------------------------

class PlanContractError(NovaError):
    """
    A Plan produced by the Planner (Kimi) references a tool or worker
    that does not exist in the registry. This is a contract failure,
    not a runtime/execution failure — it is detected before the plan
    is handed to the Router, so no locks are ever acquired for it.
    """

    def __init__(
        self,
        message: str,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
    ):
        self.plan_id = plan_id
        self.step_id = step_id
        self.raw_value = raw_value
        super().__init__(message)


class WorkerNotFoundError(PlanContractError):
    """
    Step.tool_or_worker looked like a worker (the "worker_" naming
    convention matched) but no worker with that exact name exists in
    the worker registry.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
        available_workers: List[str],
    ):
        self.available_workers = available_workers
        message = (
            f"Step '{step_id}' references unknown worker '{raw_value}'. "
            f"Available workers: {', '.join(available_workers) or '(none registered)'}"
        )
        super().__init__(message, plan_id, step_id, raw_value)


class ToolNotFoundError(PlanContractError):
    """
    Step.tool_or_worker did not match the worker naming convention but
    no primitive tool with that exact name exists in the tool registry.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
        available_tools: List[str],
    ):
        self.available_tools = available_tools
        message = (
            f"Step '{step_id}' references unknown tool '{raw_value}'. "
            f"Available tools: {', '.join(available_tools) or '(none registered)'}"
        )
        super().__init__(message, plan_id, step_id, raw_value)


# ---------------------------------------------------------------------------
# RESERVED FAMILIES — named, not implemented. Do not populate before
# their corresponding design session (see NOVA_CLI_MVP_ROADMAP.md).
# ---------------------------------------------------------------------------

class ExecutionError(NovaError):
    """
    RESERVED for Fase 3+ (error_policy.py — retry / model fallback /
    escalate). Intentionally empty. Owned by core/director/, not by
    the Iniciador.
    """


class DivergenceError(NovaError):
    """
    RESERVED for Fase 5 (divergence_comparator.py + reviewer.py).
    Intentionally empty. Owned by core/director/.
    """

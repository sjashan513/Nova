"""
Domain exceptions — the small, closed vocabulary of things that can go
wrong in Nova.

Design decision (sealed in the Fase 0 design session, June 2026; extended
in the Fase 1 / Planner Layer ADR, June 2026):

    NovaError                       # root, never raised directly
      |
      +-- PlannerError              # everything that can fail between
      |     |                       # "Kimi responded" and "we have a
      |     |                       # valid Plan object" -- raised by
      |     |                       # core/planner/planner.py
      |     |
      |     +-- PlannerResponseError    # response is not parseable JSON,
      |     |                          # or is missing the top-level keys
      |     |                          # (status / plan)
      |     +-- PlannerValidationError  # top-level shape is fine, but the
      |                                 # "plan" payload does not instantiate
      |                                 # as Plan/Step -- wraps the native
      |                                 # pydantic.ValidationError, never lets
      |                                 # it escape unwrapped
      |
      +-- PlanContractError         # the Plan IS a valid object, but a
      |     |                       # Step references something that does
      |     |                       # not exist in the registry -- caught
      |     |                       # by the Iniciador BEFORE the plan
      |     |                       # ever reaches the Router/Director
      |     |
      |     +-- WorkerNotFoundError
      |     +-- ToolNotFoundError
      |     +-- PlanContractErrorGroup  # wraps multiple unresolved
      |                                 # PlanContractErrors after the
      |                                 # Iniciador's retry budget with
      |                                 # Kimi is exhausted
      |
      +-- ExecutionError            # RESERVED — Fase 3+. Director/Worker
      |                             # runtime failures (timeouts, bad
      |                             # output shape, retries exhausted).
      |                             # Empty on purpose, do not populate yet.
      |
      +-- DivergenceError           # RESERVED — Fase 5. Raised when the
                                    # divergence comparator finds a worker's
                                    # reported `assumes` does not match what
                                    # Kimi declared, and is then judged by
                                    # the Reviewer. Empty on purpose.

Why PlannerError is a sibling of PlanContractError, not its parent or
child: these are sequential pipeline stages, not a specialization
relationship. PlannerError happens BEFORE a Plan object exists at all.
PlanContractError presupposes one already does. Nesting one inside the
other would imply a relationship that isn't there.

Why one root with families instead of a flat list:
A consumer (Iniciador today, Director/Gemma later) can catch a family
(`except PlannerError`, `except PlanContractError`) without enumerating
every subtype, while the log and Jashan still see the exact subtype that
fired. This is the same shape the Reviewer's `benign | fatal | ambiguous`
verdict will eventually map onto — we are not building that mapping yet,
just making sure the vocabulary won't have to be reshuffled when we do.

Ownership: PlannerError and PlanContractError (and all their children)
are raised and handled exclusively by the Iniciador (core/planner/). The
Director never sees a plan that failed either check — by design, a plan
that fails parsing or contract validation never reaches the Router.

Retry note (see NOVA_PLANNER_LAYER_ADR.md §2.4): PlannerError and
PlanContractError currently share a single retry counter
(MAX_CONTRACT_RETRIES) inside the Iniciador's get_plan() loop. This is a
deliberate v1 simplification, not a permanent guarantee -- see the ADR's
open-debt section before assuming independent budgets per family.
"""

from typing import List, Optional


class NovaError(Exception):
    """Root of all domain-specific errors in Nova. Never raised directly."""


# ---------------------------------------------------------------------------
# PlannerError family — Fase 1. Owned by the Iniciador.
#
# Covers everything that can go wrong with Kimi's raw response BEFORE a
# valid Plan object exists. Sequential to, not a parent/child of,
# PlanContractError below.
# ---------------------------------------------------------------------------

class PlannerError(NovaError):
    """
    Umbrella for failures in the Planner's raw response, before a Plan
    object can be said to exist. Raised by core/planner/planner.py,
    caught by the Iniciador's retry loop in core/planner/iniciador.py.
    """

    def __init__(self, message: str, raw_response: Optional[str] = None):
        self.raw_response = raw_response
        super().__init__(message)


class PlannerResponseError(PlannerError):
    """
    Kimi's response is not parseable as JSON, or parses but is missing
    the expected top-level keys ("status", "plan"). This is a format
    failure -- it says nothing yet about whether the plan itself, if
    any, is well-formed.
    """

    def __init__(self, message: str, raw_response: Optional[str] = None):
        super().__init__(message, raw_response)


class PlannerValidationError(PlannerError):
    """
    Kimi's response has the correct top-level shape (status/plan keys
    present), but the content of "plan" does not instantiate as a valid
    Plan/Step. Wraps the native pydantic.ValidationError -- callers
    should never see a raw pydantic error escape the planner layer.
    """

    def __init__(
        self,
        message: str,
        raw_response: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        self.original_error = original_error
        super().__init__(message, raw_response)


# ---------------------------------------------------------------------------
# PlanContractError family — Fase 0, extended Fase 1. Owned by the
# Iniciador.
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


class PlanContractErrorGroup(PlanContractError):
    """
    Raised by the Iniciador's retry loop (get_plan()) when
    MAX_CONTRACT_RETRIES is exhausted and one or more PlanContractErrors
    remain unresolved. Wraps all of them -- callers must never lose
    errors by only propagating the first one and logging the rest.

    Note this does NOT collect PlannerError instances (PlannerResponseError /
    PlannerValidationError) -- those are a separate, earlier pipeline
    stage. See NOVA_PLANNER_LAYER_ADR.md §2.2 for why they are siblings,
    not nested.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        errors: List[PlanContractError],
    ):
        self.errors = errors
        step_ids = ", ".join(e.step_id for e in errors) or "(none)"
        message = (
            f"Plan contract validation failed after exhausting retries. "
            f"{len(errors)} unresolved error(s) in step(s): {step_ids}"
        )
        # PlanContractErrorGroup itself doesn't map to one single
        # step/raw_value -- it IS the group. step_id/raw_value below are
        # placeholders so the parent __init__ contract is satisfied;
        # callers should inspect `.errors` for the real detail, not these.
        first = errors[0] if errors else None
        super().__init__(
            message,
            plan_id=plan_id,
            step_id=first.step_id if first else "(none)",
            raw_value=first.raw_value if first else "(none)",
        )


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

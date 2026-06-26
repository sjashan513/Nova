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
      |     |                       # not exist in the registry, or the
      |     |                       # depends_on graph itself is broken --
      |     |                       # caught BEFORE the plan ever reaches
      |     |                       # the Router/Director
      |     |
      |     +-- WorkerNotFoundError
      |     +-- ToolNotFoundError
      |     +-- InvalidStepReferenceError   # depends_on references a
      |     |                               # step_id that doesn't exist
      |     |                               # in the plan (Fase 2)
      |     +-- CyclicDependencyError       # depends_on graph has a
      |     |                               # cycle, no valid execution
      |     |                               # order exists (Fase 2)
      |     +-- PlanContractErrorGroup  # wraps multiple unresolved
      |                                 # PlanContractErrors after the
      |                                 # Iniciador's retry budget with
      |                                 # Kimi is exhausted
      |
      +-- ExecutionError            # Fase 2. Director/Worker runtime
      |     |                       # failures -- detected DURING
      |     |                       # execution, contrast with
      |     |                       # PlanContractError (detected
      |     |                       # BEFORE execution starts)
      |     |
      |     +-- StepExecutionError      # one failed attempt, wraps the
      |     |                           # real underlying exception
      |     +-- RetriesExhaustedError   # Level 1 retry budget spent,
      |                                 # carries every attempt's error
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


class InvalidStepReferenceError(PlanContractError):
    """
    A Step's depends_on lists a step_id that does not exist anywhere
    else in the same plan. Same spirit as WorkerNotFoundError /
    ToolNotFoundError: the plan is not executable as given, detected
    before the Director ever attempts to build execution levels or
    acquire any lock.

    This was named (but not implemented) as a future example in
    NOVA_DOMAIN_LAYER_ADR.md §2.3 -- this is that future arriving in
    Fase 2, when core/director/dag.py needed it for real.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
        known_step_ids: List[str],
    ):
        self.known_step_ids = known_step_ids
        message = (
            f"Step '{step_id}' depends on unknown step_id '{raw_value}'. "
            f"Known step ids in this plan: {', '.join(known_step_ids) or '(none)'}"
        )
        super().__init__(message, plan_id, step_id, raw_value)


class UndeclaredDependencyError(PlanContractError):
    """
    A Step's `input` contains a "$step_id.field" reference to a
    step_id that exists in the plan, but is NOT listed in this same
    step's own `depends_on`. Distinct from InvalidStepReferenceError,
    which is about depends_on pointing at something that doesn't exist
    at all -- this is about input and depends_on disagreeing with each
    other on a step that DOES exist.

    Why this matters operationally, not just stylistically: the
    Director only guarantees a step's result is in context once that
    step is a declared prerequisite (core/director/dag.py builds
    execution levels strictly from depends_on). A step that references
    another step's result without depending on it could be dispatched
    in the same level as -- or even before -- the step it's reading
    from, since the level-building algorithm never saw that dependency
    declared. The reference might resolve by coincidence in some
    executions and fail in others, depending on dispatch timing -- the
    kind of intermittent failure that's expensive to debug later.
    Caught here, before any execution, makes it a deterministic
    contract failure instead.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
        declared_depends_on: List[str],
    ):
        self.declared_depends_on = declared_depends_on
        message = (
            f"Step '{step_id}' input references step '{raw_value}' via "
            f"\"${raw_value}.<field>\", but '{raw_value}' is not in "
            f"this step's own depends_on {declared_depends_on}. Add it to "
            f"depends_on, or the referenced step's result is not "
            f"guaranteed to exist when this step runs."
        )
        super().__init__(message, plan_id, step_id, raw_value)


class CyclicDependencyError(PlanContractError):
    """
    The plan's depends_on graph contains a cycle (directly or
    transitively) -- no valid execution order exists. Detected by
    core/director/dag.py's level-building pass: if a pass over the
    remaining steps finds none whose dependencies are all already
    placed, but steps remain unplaced, that is only possible if the
    graph has a cycle (assuming InvalidStepReferenceError already
    ruled out dangling references).
    """

    def __init__(
        self,
        plan_id: Optional[str],
        cyclic_step_ids: List[str],
    ):
        self.cyclic_step_ids = cyclic_step_ids
        message = (
            f"Plan contains a dependency cycle involving step(s): "
            f"{', '.join(cyclic_step_ids)}"
        )
        # No single step/raw_value applies to a cycle as a whole --
        # same placeholder pattern as PlanContractErrorGroup above.
        first_id = cyclic_step_ids[0] if cyclic_step_ids else "(none)"
        super().__init__(message, plan_id=plan_id, step_id=first_id, raw_value=first_id)


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
# ExecutionError family — Fase 2. Owned by core/director/error_policy.py,
# NOT by the Iniciador (contrast with PlannerError/PlanContractError,
# which the Iniciador owns exclusively).
#
# Populates the error-policy Level 1 (automatic retry, transient) and
# the minimal SHAPE of Level 3 (escalate, retries exhausted) from
# NOVA_DIRECTOR_LAYER_ADR.md §4.3. Level 2 (model fallback) is NOT
# implemented here -- it only makes sense for LLM-powered Worker steps,
# which do not exist yet in Fase 2 (filesystem/terminal tools only, no
# LLM calls). Fase 3 EXTENDS this family with a Level-2 case; it does
# not replace what Fase 2 builds.
# ---------------------------------------------------------------------------

class ExecutionError(NovaError):
    """
    Umbrella for failures that happen DURING step execution -- as
    opposed to PlanContractError, which is detected BEFORE execution
    ever starts. A step that fails here already passed every contract
    check; the failure is about what happened when its tool/worker
    actually ran (or failed to).
    """

    def __init__(self, message: str, step_id: Optional[str] = None):
        self.step_id = step_id
        super().__init__(message)


class StepExecutionError(ExecutionError):
    """
    A single execution attempt of a step's tool/worker call raised.
    Raised by core/director/error_policy.py on each failed attempt,
    BEFORE deciding whether to retry -- this is the per-attempt
    failure, not the final outcome. Wraps the original exception so
    callers can inspect what actually went wrong (a real OSError from
    a locked file, a subprocess timeout, etc.) without that exception
    type leaking into code that only knows about NovaError.
    """

    def __init__(
        self,
        step_id: str,
        attempt: int,
        original_error: Exception,
    ):
        self.attempt = attempt
        self.original_error = original_error
        message = (
            f"Step '{step_id}' failed on attempt {attempt}: "
            f"{type(original_error).__name__}: {original_error}"
        )
        super().__init__(message, step_id=step_id)


class RetriesExhaustedError(ExecutionError):
    """
    Level 1 (automatic retry) ran out of attempts without the step
    succeeding. This is the trigger for Level 3 (escalate) in Fase 2's
    minimal form: no Gemma, no Ventanilla exist yet, so "escalate"
    means the Plan is marked FAILED and the CLI prints this error's
    detail -- same concept the ADR describes, simpler destination
    because the UX pieces it was written for don't exist yet. Fase 3+
    can change what happens AFTER this is raised without changing
    when it's raised.

    Carries every StepExecutionError from every attempt, not just the
    last one -- same "never silently drop an error" principle as
    PlanContractErrorGroup (Fase 0/1).
    """

    def __init__(
        self,
        step_id: str,
        attempts: List[StepExecutionError],
    ):
        self.attempts = attempts
        message = (
            f"Step '{step_id}' failed after {len(attempts)} attempt(s). "
            f"Last error: {attempts[-1].original_error if attempts else '(none)'}"
        )
        super().__init__(message, step_id=step_id)


# ---------------------------------------------------------------------------
# RESERVED FAMILIES — named, not implemented. Do not populate before
# their corresponding design session (see NOVA_CLI_MVP_ROADMAP.md).
# ---------------------------------------------------------------------------

class DivergenceError(NovaError):
    """
    RESERVED for Fase 5 (divergence_comparator.py + reviewer.py).
    Intentionally empty. Owned by core/director/.
    """

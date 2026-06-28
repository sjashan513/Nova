"""
ExecutionError family — Fase 2, extended Fase 3. Owned by
core/director/error_policy.py, NOT by the Iniciador (contrast with
PlannerError/PlanContractError, which the Iniciador owns exclusively).

Populates the error-policy Level 1 (automatic retry, transient) and
the minimal SHAPE of Level 3 (escalate, retries exhausted) from
NOVA_DIRECTOR_LAYER_ADR.md §4.3. Level 2 (model fallback) is a Fase 3
addition (see NOVA_WORKER_LAYER_ADR.md §2.5) -- it lives in
core/director/error_policy.py as its own function, not as a new
exception subtype here, since a fallback attempt that itself fails
still raises the same RetriesExhaustedError as any other exhausted
step.

Fase 3 addition: WorkerExecutionError. See its own docstring below for
why it is a sibling of RetriesExhaustedError, not of StepExecutionError.
"""

from typing import List, Optional

from core.domain.exceptions.nova_error import NovaError


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


class WorkerExecutionError(ExecutionError):
    """
    Fase 3. Raised by workers/base.py::BaseWorker.run() when a
    Worker's own execute() returns status: "error" -- meaning the
    Worker exhausted ITS OWN internal error policy (its own
    "ecosystem", per NOVA_PRIMITIVES_ADR.md §3.5: every Worker may be
    arbitrarily complex inside, with whatever retry/fallback logic
    makes sense for its specific job) and could not produce a result.

    Deliberately NOT raised per-attempt the way StepExecutionError is
    -- this is not "one attempt failed, here's attempt N of M". By the
    time this fires, the Worker has already decided, on its own terms,
    that nothing more can be done. That is why this is a sibling of
    RetriesExhaustedError (the OUTCOME of a budget being spent),
    rather than of StepExecutionError (ONE step in spending that
    budget): semantically, a WorkerExecutionError IS already an
    exhausted-budget event, just one whose budget was spent inside the
    Worker instead of inside core/director/error_policy.py's own loop.

    This is exactly why error_policy.py's execute_with_retry has a
    dedicated except clause for this type, checked BEFORE the generic
    except Exception: retrying the same model on a problem the Worker
    itself already gave up on would spend real NIM calls attacking a
    cause retrying never changes. What it does NOT skip is Level 2
    (model fallback, a separate function layered on top of
    execute_with_retry) -- a different model is a genuinely different
    condition, not "the same thing again," so it still gets its own
    chance.

    Carries only `reason` -- a human-readable string explaining what
    the Worker could not resolve. If the Worker's own execute() caught
    a real exception internally (e.g. a network error during its own
    retry loop), that exception is serialized into this same string by
    the Worker before returning status: "error" -- there is no second,
    separate field for "the underlying exception object", by design
    (same principle StepExecutionError already follows: wrap the real
    cause into a message, don't invent a parallel shape for it).
    """

    def __init__(self, reason: str):
        self.reason = reason
        message = f"Worker exhausted its own error policy: {reason}"
        super().__init__(message, step_id=None)


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
    PlanContractErrorGroup (Fase 0/1). Fase 3 note: when this is raised
    because of a WorkerExecutionError short-circuit (see
    error_policy.py), `attempts` contains exactly one StepExecutionError
    wrapping that WorkerExecutionError -- not a full max_retries-sized
    list, since no backoff/retry budget was actually spent.
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

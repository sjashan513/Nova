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

Fase 4b addition: PlanAbortedError. See its own docstring below for
why it is a sibling of RetriesExhaustedError and NOT of
WorkerExecutionError.
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


class PlanAbortedError(ExecutionError):
    """
    Fase 4b. Raised by tools/vscode.py::show_diff() when Jashan
    explicitly rejects a proposed diff via the VSCode Diff Editor
    (Ctrl+Shift+N).

    Semantically distinct from both WorkerExecutionError and
    RetriesExhaustedError:
      - WorkerExecutionError: a technical failure — something went wrong
        that the system could not resolve on its own.
      - RetriesExhaustedError: a retry budget spent — the system tried
        its best and ran out of attempts.
      - PlanAbortedError: a conscious human decision — nothing failed,
        nothing needs retrying. Jashan looked at the proposed change and
        chose not to apply it. Retrying would be wrong by definition.

    This is why the Director catches PlanAbortedError in a SEPARATE
    except clause BEFORE the generic exception handler, sets
    self.status = "ABORTED" (not "FAILED"), and never routes this
    through execute_with_retry or execute_with_fallback. The distinction
    between ABORTED and FAILED matters at the CLI level: FAILED means
    "something broke, you may want to retry"; ABORTED means "you
    stopped this, it is not a bug."

    Carries only `message` — a plain string describing which file's
    diff was rejected. No step_id at construction time (the tool does
    not know its own step_id), so step_id is left as None and may be
    enriched by the Director if needed in a future phase.
    """

    def __init__(self, message: str):
        super().__init__(message, step_id=None)


class AssumesFailedError(ExecutionError):
    """
    Fase 5. Raised by core/director/comparator.py::check() when one or
    more implicit_assumes for a step evaluate to False before the step
    is dispatched.

    Semantically distinct from all existing ExecutionError subtypes:
      - StepExecutionError:  a tool/worker call attempted and failed.
      - WorkerExecutionError: a worker exhausted its own internal policy.
      - RetriesExhaustedError: the Director's retry budget ran out.
      - PlanAbortedError: a conscious human rejection (diff gate).
      - AssumesFailedError: the world no longer matches what the Planner
        assumed when it generated the plan. The step was never attempted.
        Nothing failed — the precondition for attempting it was not met.

    This is why the Director catches AssumesFailedError in a SEPARATE
    except clause BEFORE RetriesExhaustedError and PlanAbortedError,
    sets self.status = "PAUSED" (not "FAILED", not "ABORTED"), and
    never routes this through execute_with_retry or
    execute_with_fallback. Retrying the same step against the same
    broken precondition is pointless — a human decision is required
    first (retry after fixing the precondition | skip | abort).

    The distinction between PAUSED, ABORTED, and FAILED matters at the
    CLI level:
      FAILED  → something broke, may be transient, consider retrying.
      ABORTED → you stopped this consciously, it is not a bug.
      PAUSED  → the world changed since planning; your input is needed
                before execution can continue.

    Carries `step_id` and `failures` — the list of AssumeFailure
    dataclasses from the Comparador, each with op, reason, and the raw
    assume dict. The CLI uses these to present exactly what diverged
    and where, without having to re-evaluate anything.
    """

    def __init__(self, step_id: str, failures: list):
        self.failures = failures
        reasons = "; ".join(f.reason for f in failures)
        message = (
            f"Step '{step_id}' cannot run — precondition(s) not met: {reasons}"
        )
        super().__init__(message, step_id=step_id)

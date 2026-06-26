
from .nova_error import NovaError
from typing import List, Optional


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

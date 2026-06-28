"""
Error policy for step execution -- Level 1 (automatic retry) and the
minimal shape of Level 3 (escalate), per NOVA_DIRECTOR_LAYER_ADR.md
§4.3's three-level error policy:

    Level 1 -- Automatic retry (transparent)
      Step fails -> retries < max_retries -> re-queue with exponential
      backoff: 2s, 4s, 8s

      Fase 3 exception: if the failure is a WorkerExecutionError (the
      Worker's OWN internal error policy is already exhausted -- see
      core/domain/exceptions/execution_errors.py), this function does
      NOT spend the backoff/retry budget on it. Retrying the exact
      same model on a problem the Worker itself already gave up on
      attacks no different cause and only spends real NIM calls. See
      the dedicated except clause below.

    Level 2 -- Model fallback (transparent)
      NOT implemented in this function. Lives in its own function in
      this same module (see NOVA_WORKER_LAYER_ADR.md §2.5 -- a
      separate function layered on top of execute_with_retry, not an
      extension of it). It still gets a chance even when Level 1 was
      short-circuited by a WorkerExecutionError: a different model is
      a genuinely different condition, not "the same thing again."

    Level 3 -- User decision
      All else failed, OR step is critical: true
      -> Plan paused, Jashan notified, options: retry | skip | abort

Fase 2's Level 3 is deliberately minimal: Gemma and Ventanilla don't
exist yet, so "notified" means RetriesExhaustedError propagates up to
the Director, which marks the Plan FAILED, and the CLI prints the
error's detail. The "retry | skip | abort" decision flow is not built
here -- that needs UX pieces this phase doesn't have. What Fase 2 DOES
guarantee is that the failure is never silent and never loses
information about what was tried.

Deliberately decoupled from any specific tool: execute_with_retry
takes any zero-argument callable. This lets it be tested today against
a fake function, before tools/filesystem.py or tools/terminal.py exist
(Fase 2 task T5) -- same "test each piece against a fake of the next
one" principle as the rest of this codebase (NOVA_CLI_MVP_ROADMAP.md
§0.3). The Director (T6) is what supplies a real callable bound to a
real tool call. As of Fase 3, that real callable may be a Worker's
run() (workers/base.py::BaseWorker.run()) -- this function does not
need to know or care whether `fn` ends up calling a primitive tool or
a Worker; the only thing that changed is one new except clause for one
new exception type a Worker's run() can raise.
"""

import time
from typing import Any, Callable, Dict, List, Optional

from core.domain.exceptions import (
    StepExecutionError,
    WorkerExecutionError,
    RetriesExhaustedError,
)

# (2s, 4s, 8s) -- sealed in NOVA_DIRECTOR_LAYER_ADR.md §4.3, not a
# guess. Fixed here as the v1 default; not yet exposed as a per-Step
# config knob (the ADR's Step.max_retries field is not on the schema
# yet -- see Fase 3's extension of this module).
DEFAULT_BACKOFF_SECONDS = (2, 4, 8)


def execute_with_retry(
    fn: Callable[[], Dict[str, Any]],
    step_id: str,
    max_retries: int = 3,
    backoff_seconds: tuple = DEFAULT_BACKOFF_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """
    Calls `fn()` up to `max_retries` times, sleeping with exponential
    backoff between failed attempts. Returns fn()'s return value on
    the first successful call.

    `sleep_fn` is injectable specifically so tests don't have to
    actually wait through real backoff delays -- production code never
    needs to pass this, the default is the real time.sleep.

    Does NOT catch and retry NovaError subclasses other than what `fn`
    itself raises as a plain Exception -- if `fn` raises a
    PlanContractError (or any contract-detection error from T1-T3),
    that is a structural failure, not a transient one, and retrying it
    would never succeed differently. This function has no special
    case for that, because by the time the Director calls a step's
    tool/worker, contract validation has ALREADY happened (the
    Iniciador and dag.py's checks run before any execution is
    attempted) -- a PlanContractError reaching this function at all
    would indicate an invariant was violated upstream, not something
    this function should paper over with a retry.

    Fase 3: DOES have a special case for WorkerExecutionError, checked
    BEFORE the generic except Exception below. When `fn` raises this
    (via a Worker's BaseWorker.run()), the Worker has already
    exhausted its OWN internal error policy -- this function builds a
    single StepExecutionError wrapping it and raises
    RetriesExhaustedError immediately, without sleeping or spending
    the remaining attempts on identical retries that would attack no
    different cause. `attempts` on the resulting RetriesExhaustedError
    therefore has exactly one entry in this case, not max_retries-many
    -- this is expected, not a bug: it accurately reflects that no
    backoff/retry budget was actually spent. Level 2 (model fallback,
    a separate function in this module) still runs afterward and gets
    its own real chance, since a different model is a genuinely
    different condition.

    Raises:
        RetriesExhaustedError: every attempt failed (Level 1's normal
            path), OR a single WorkerExecutionError short-circuited
            the loop (Fase 3's path, above). Carries one
            StepExecutionError per attempt -- nothing is dropped.
    """
    attempts: List[StepExecutionError] = []

    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except WorkerExecutionError as e:
            # Worker already exhausted its own error policy -- one
            # StepExecutionError to record what happened, then
            # straight to RetriesExhaustedError. No backoff, no
            # remaining attempts spent: retrying the same model here
            # would not be attacking a different cause, it would just
            # spend more real NIM calls on a problem the Worker itself
            # already gave up on.
            step_error = StepExecutionError(
                step_id=step_id, attempt=attempt, original_error=e
            )
            raise RetriesExhaustedError(step_id=step_id, attempts=[step_error])
        except Exception as e:
            step_error = StepExecutionError(
                step_id=step_id, attempt=attempt, original_error=e
            )
            attempts.append(step_error)

            is_last_attempt = attempt == max_retries
            if not is_last_attempt:
                delay = backoff_seconds[min(
                    attempt - 1, len(backoff_seconds) - 1)]
                sleep_fn(delay)

    raise RetriesExhaustedError(step_id=step_id, attempts=attempts)


def execute_with_fallback(
    fn_factory: Callable[[Optional[str]], Callable[[], Dict[str, Any]]],
    step_id: str,
    primary_model: Optional[str],
    fallback_model: Optional[str],
    max_retries: int = 3,
    backoff_seconds: tuple = DEFAULT_BACKOFF_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """
    Level 2 (model fallback) -- NOVA_WORKER_LAYER_ADR.md §2.5. A
    separate function layered ON TOP OF execute_with_retry, not an
    extension of it: execute_with_retry stays completely ignorant of
    models, fallback or otherwise -- it still serves Steps with no
    concept of a model at all (filesystem, terminal) unchanged.

    `fn_factory` is a callable that takes a model (str, or None for a
    Step with no model at all) and returns the zero-argument callable
    execute_with_retry expects -- this is what lets the SAME logical
    call be reconstructed with a DIFFERENT model on the second
    attempt, without this function knowing anything about dispatch
    tables, resolved_input, or any other Director-internal detail.
    The Worker itself never knows two models exist; the Director
    builds two different inputs (one per model) by calling
    fn_factory twice -- see director_instance.py::_execute_step's
    lambda, which now takes `model` as a parameter precisely so this
    function can supply it twice with different values.

    Behavior:
      1. Try with primary_model via execute_with_retry (full Level 1
         budget: max_retries attempts, with backoff -- unless a
         WorkerExecutionError short-circuits it, per that function's
         own documented behavior).
      2. If that raises RetriesExhaustedError AND fallback_model is
         set, try again with fallback_model -- a fresh, full Level 1
         budget of its own. This is deliberate: a different model is
         a genuinely different condition, not "the same thing again"
         (session decision) -- it earns its own real attempts, not a
         single bare retry.
      3. If fallback_model is None, there is nothing to fall back to
         -- the original RetriesExhaustedError from step 1 propagates
         unchanged. This is expected, not an error in itself: per
         MissingModelError's own docstring, fallback_model is
         optional by design -- a Step without one simply has no
         Level 2 available and escalates straight to Level 3.
      4. If the fallback attempt ALSO raises RetriesExhaustedError,
         that second error is what propagates -- it already carries
         its own attempts list (the fallback model's own attempts),
         which is the information that actually matters at this
         point: what happened with the LAST model tried.

    Steps with no model at all (primary_model is None -- a primitive
    tool, not a Worker) should not call this function in the first
    place; the Director only routes Worker steps through it. This
    function does not special-case None defensively beyond what
    naturally happens: fn_factory(None) would simply be called, and a
    Worker that doesn't read "model" from its input is unaffected --
    this codepath is for Worker steps specifically, the caller is
    responsible for only invoking it then.
    """
    try:
        return execute_with_retry(
            fn_factory(primary_model),
            step_id,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            sleep_fn=sleep_fn,
        )
    except RetriesExhaustedError:
        if fallback_model is None:
            raise

        return execute_with_retry(
            fn_factory(fallback_model),
            step_id,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            sleep_fn=sleep_fn,
        )

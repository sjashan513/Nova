"""
Error policy for step execution -- Level 1 (automatic retry) and the
minimal shape of Level 3 (escalate), per NOVA_DIRECTOR_LAYER_ADR.md
§4.3's three-level error policy:

    Level 1 -- Automatic retry (transparent)
      Step fails -> retries < max_retries -> re-queue with exponential
      backoff: 2s, 4s, 8s

    Level 2 -- Model fallback (transparent)
      NOT implemented here. Only applies to LLM-powered Worker steps,
      none of which exist in Fase 2 (filesystem/terminal tools only).
      Fase 3 extends this module with a Level 2 case; it does not
      replace what's built here.

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
real tool call.
"""

import time
from typing import Any, Callable, Dict, List, Optional

from core.domain.exceptions import StepExecutionError, RetriesExhaustedError

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

    Raises:
        RetriesExhaustedError: every attempt failed. Carries one
            StepExecutionError per attempt -- nothing is dropped.
    """
    attempts: List[StepExecutionError] = []

    for attempt in range(1, max_retries + 1):
        try:
            return fn()
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

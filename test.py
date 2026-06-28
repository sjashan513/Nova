"""
T9 closure test -- forces a REAL failure through the REAL Director
pipeline (DirectorInstance, not a direct Worker call like
run_real_worker_test.py / run_t10_closure_test.py) to confirm Level 1
and Level 2 of the error policy actually absorb it, instead of it
propagating as a raw crash the way the timeout did in the T10 run
before the timeout fix (that one never went through the Director at
all -- this one does, on purpose).

Mechanism: a single-Step Plan, built by hand (same pattern as Fase 2's
own closure script -- a hand-written Plan, not going through Kimi,
since this tests the EXECUTION layer specifically, not planning).
The Step's `model` is a deliberately invalid string -- NIM should
reject it with a real API error. The Step's `fallback_model` is
minimax-m3, the model already confirmed working in T10. Expected
chain, mechanically:

    1. _execute_step routes through execute_with_fallback (step.model
       is set).
    2. First attempt: fn_factory(invalid_model) -> WorkerTsFix.run()
       -> execute() calls call_nim(model=invalid_model, ...) -> NIM
       rejects it -> a known exception type fires (HTTPError via
       raise_for_status, or KeyError/IndexError if the response body
       is malformed instead of a clean 4xx) -> execute() returns
       status: "error" -> BaseWorker.run() raises WorkerExecutionError.
    3. error_policy.py's execute_with_retry catches WorkerExecutionError
       specifically (before the generic except) -- ONE StepExecutionError,
       immediate RetriesExhaustedError, NO backoff spent retrying a
       model that will never exist.
    4. execute_with_fallback catches that RetriesExhaustedError,
       sees fallback_model is set, retries with fn_factory(fallback_model)
       -- a full, fresh Level 1 budget with the REAL model.
    5. This second attempt succeeds for real (same call that already
       worked in T10) -- the Plan completes DONE, the bad primary
       model never surfaces as a user-facing failure.

Run from the repo root:

    python run_t9_closure_test.py

Same requirements as the other Nivel 3 scripts (NIM_API_KEY,
PulseSandbox project entry, npm install in the fixture).
"""

import time

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from core.domain.models import Plan, Step
from core.domain.exceptions import RetriesExhaustedError, PlanContractErrorGroup
from core.director.director_instance import DirectorInstance
from workers.coding.worker_ts_check import WorkerTsCheck

PROJECT = "PulseSandbox"
REAL_MODEL = "minimaxai/minimax-m3"
FAKE_MODEL = "definitely-not-a-real-model/fake-v0"
SIGNAL_TS_PATH = "test_fixtures/pulse_sandbox/signal.ts"


def main() -> None:
    print("=== Step 0: get real, current errors from signal.ts (real tsc) ===")
    check_output = WorkerTsCheck().run({"project": PROJECT})
    print(check_output)

    errors = check_output["errors"]
    if not errors:
        print(
            "\nNo errors found -- signal.ts needs its deliberate type "
            "error back before this test means anything (T10's run "
            "only wrote signal.fixed.ts, .documented.ts, .test.ts -- "
            "it should not have touched signal.ts itself)."
        )
        return

    with open(SIGNAL_TS_PATH, "r", encoding="utf-8") as f:
        file_content = f.read()

    plan = Plan(
        objective=(
            "T9 closure test: deliberately invalid primary model, "
            "real fallback_model -- confirms Level 1 short-circuits "
            "on WorkerExecutionError and Level 2 recovers for real."
        ),
        steps=[
            Step(
                id="s1",
                description="Fix the real type error in signal.ts.",
                tool_or_worker="worker_ts_fix",
                action="",
                depends_on=[],
                input={
                    "project": PROJECT,
                    "file_content": file_content,
                    "errors": errors,
                },
                assumes=[],
                model=FAKE_MODEL,
                fallback_model=REAL_MODEL,
            )
        ],
    )

    print(
        f"\n=== Running through DirectorInstance: model='{FAKE_MODEL}' "
        f"(expected to fail), fallback_model='{REAL_MODEL}' ==="
    )

    director = DirectorInstance(plan)
    start = time.monotonic()

    try:
        summary = director.run()
    except (RetriesExhaustedError, PlanContractErrorGroup) as e:
        elapsed = time.monotonic() - start
        print(
            f"\n*** Plan FAILED after {elapsed:.1f}s -- Level 2 did NOT "
            f"recover. This means either fallback_model itself also "
            f"failed, or something in the chain is broken. ***"
        )
        print(f"Director status: {director.status}")
        print(f"Error: {type(e).__name__}: {e}")
        if isinstance(e, RetriesExhaustedError):
            for attempt in e.attempts:
                print(f"  attempt {attempt.attempt}: {attempt.original_error}")
        return

    elapsed = time.monotonic() - start
    print(
        f"\n=== Plan DONE in {elapsed:.1f}s -- Level 2 fallback recovered. ===")
    print(f"Director status: {director.status}")
    print(f"Step 's1' result: {summary['context']['s1']}")


if __name__ == "__main__":
    main()

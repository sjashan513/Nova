"""
T10 closure test -- chained real-NIM run of
worker_ts_check -> worker_ts_fix -> worker_jsdoc -> worker_test_writer,
all against the same fixture file, each step operating on the
PREVIOUS step's output content, never on the original file again
after Step 2. This is NOVA_PRIMITIVES_ADR.md §3.4's "sequence, don't
parallelize, when one worker's output would invalidate another's
premise" enforced by construction -- there is no real Plan/
DirectorInstance run here, just direct Worker calls in the exact
order T10 requires, so the script itself IS the sequencing.

Nivel 3 -- real NIM calls (3 of them), real tsc, real cost. Run from
the repo root:

    python run_t10_closure_test.py

Same requirements as run_real_worker_test.py:
    - NIM_API_KEY in the environment (.env, loaded below if
      python-dotenv is installed).
    - npm install already run once inside
      test_fixtures/pulse_sandbox/.
    - The "PulseSandbox" entry in registry/project_registry.yaml
      (added this session, isolated from the real "Pulse" entry).

Writes each step's real output to disk as it goes
(signal.fixed.ts, signal.documented.ts, signal.test.ts) so the actual
artifacts can be opened and read, not just trusted from a printed
dict.
"""

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from workers.coding.worker_ts_check import WorkerTsCheck
from workers.coding.worker_ts_fix import WorkerTsFix
from workers.coding.worker_jsdoc import WorkerJsdoc
from workers.coding.worker_test_writer import WorkerTestWriter

PROJECT = "PulseSandbox"
MODEL = "minimaxai/minimax-m3"
FIXTURE_DIR = "test_fixtures/pulse_sandbox"
GENERATED_DIR = f"{FIXTURE_DIR}/_generated"
SIGNAL_TS_PATH = f"{FIXTURE_DIR}/signal.ts"


def _write(path: str, content: str) -> None:
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  -> wrote {path}")


def main() -> None:
    print("=== Step 1: WorkerTsCheck (real tsc) ===")
    check_output = WorkerTsCheck().run({"project": PROJECT})
    print(check_output)

    errors = check_output["errors"]
    if not errors:
        print(
            "\nNo errors found -- check signal.ts still has its "
            "deliberate type error before continuing the chain."
        )
        return

    with open(SIGNAL_TS_PATH, "r", encoding="utf-8") as f:
        original_content = f.read()

    print(f"\n=== Step 2: WorkerTsFix (real NIM call, model='{MODEL}') ===")
    fix_output = WorkerTsFix().run(
        {
            "project": PROJECT,
            "model": MODEL,
            "file_content": original_content,
            "errors": errors,
        }
    )
    print(fix_output)
    fixed_content = fix_output["fixed_content"]
    _write(f"{GENERATED_DIR}/signal.fixed.ts", fixed_content)

    print(f"\n=== Step 3: WorkerJsdoc (real NIM call, model='{MODEL}') ===")
    # Operates on the FIXED content, not the original -- jsdoc must
    # never describe code that fix is about to change underneath it.
    jsdoc_output = WorkerJsdoc().run(
        {
            "project": PROJECT,
            "model": MODEL,
            "file_content": fixed_content,
        }
    )
    print(jsdoc_output)
    documented_content = jsdoc_output["documented_content"]
    _write(f"{GENERATED_DIR}/signal.documented.ts", documented_content)

    print(
        f"\n=== Step 4: WorkerTestWriter (real NIM call, model='{MODEL}') ===")
    # Operates on the DOCUMENTED content -- the final state of the
    # file after both prior workers, same sequencing principle.
    test_output = WorkerTestWriter().run(
        {
            "project": PROJECT,
            "model": MODEL,
            "file_content": documented_content,
        }
    )
    print(test_output)
    _write(f"{GENERATED_DIR}/signal.test.ts", test_output["test_content"])

    print(
        f"\n=== Done. {test_output['test_count']} test(s) written. "
        f"Inspect {GENERATED_DIR}/signal.fixed.ts, "
        f".documented.ts, and .test.ts. ==="
    )


if __name__ == "__main__":
    main()

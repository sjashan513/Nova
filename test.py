"""
Standalone smoke test for core/planner/iniciador.py against the REAL
NIM API -- this is the test that matters: not "does the mock behave",
but "does Kimi actually self-correct when given real registry context
after a real contract failure". The mocked unit tests already proved
the loop's mechanics are correct; this proves the mechanism is useful.

Run from the project root:
    python test_iniciador_live.py

Requires NIM_API_KEY in the environment (see test_planner_live.py).
"""

import os

from core.planner.iniciador import Iniciador
from core.domain.exceptions import PlanContractErrorGroup, PlannerError

if not os.environ.get("NIM_API_KEY"):
    raise SystemExit("NIM_API_KEY is not set.")

ini = Iniciador()

task = "fix the TypeScript errors in signal.ts"
print(f"Calling Iniciador.get_plan with task: {task!r}\n")

try:
    result = ini.get_plan(task)
    print(f"status: {result['status']}")
    print(f"Raw response: {result}\n")
    if result["status"] == "ready":
        plan = result["plan"]
        print(f"objective: {plan.objective}")
        for step in plan.steps:
            print(f"  [{step.id}] {step.tool_or_worker} -- {step.description}")
    else:
        print(f"questions: {result['questions']}")
except PlanContractErrorGroup as e:
    print(
        f"PlanContractErrorGroup after exhausting retries: {len(e.errors)} unresolved")
    for err in e.errors:
        print(
            f"  - {type(err).__name__}: step={err.step_id} raw_value={err.raw_value}")
except PlannerError as e:
    print(f"PlannerError (not recovered): {e}")

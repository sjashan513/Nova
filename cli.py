import sys

from core.planner.iniciador import Iniciador
from core.domain.exceptions import PlanContractErrorGroup, PlannerError, PlanContractError


def _print_plan(plan) -> None:
    print(f"\nObjective: {plan.objective}")
    print(f"Steps ({len(plan.steps)}):")
    for step in plan.steps:
        print(f"  [{step.id}] {step.tool_or_worker} -- {step.description}")
        if step.assumes:
            print(f"      assumes: {', '.join(step.assumes)}")


def _print_clarification(questions) -> None:
    print("\nNova needs clarification before planning this task:")
    for i, q in enumerate(questions, start=1):
        print(f"  {i}. {q}")


def _print_contract_error_group(e: PlanContractErrorGroup) -> None:
    print(
        f"\n[PlanContractErrorGroup] Could not produce a valid plan after "
        f"retrying with Kimi. {len(e.errors)} unresolved error(s):"
    )
    for err in e.errors:
        print(
            f"  - [{type(err).__name__}] step '{err.step_id}': '{err.raw_value}'")


def _print_planner_error(e: PlannerError) -> None:
    print(f"\n[{type(e).__name__}] {e}")
    if e.raw_response:
        print(f"  raw_response: {e.raw_response!r}")


def main():
    print("Nova CLI v1.0 - Initialized (Phase 1: Planner connected)")
    print("Type 'exit' or 'quit' to terminate.")

    iniciador = Iniciador()

    while True:
        try:
            user_input = input("\nNova> ").strip()

            if not user_input:
                continue

            command = user_input.lower()

            if command in ['exit', 'quit']:
                print("Shutting down systems. See you next time!")
                break

            if command == 'help':
                print("Basic commands:")
                print("  exit - Close the application")
                print("  help - Show this message")
                print("  [text] - Sends the task to the Planner (Kimi via NIM)")
                continue

            print(f"Planning: {user_input}")

            try:
                response = iniciador.get_plan(user_input)
            except PlanContractErrorGroup as e:
                # Most specific exception first -- PlanContractErrorGroup
                # IS a PlanContractError, so it must be caught before the
                # broader PlanContractError handler below, or this branch
                # would never be reached.
                _print_contract_error_group(e)
                continue
            except PlannerError as e:
                _print_planner_error(e)
                continue
            except PlanContractError as e:
                # A single, non-grouped PlanContractError should not
                # normally escape the Iniciador's retry loop (it always
                # wraps into a Group on exhaustion) -- caught here only
                # as a safety net, not an expected path.
                print(f"\n[{type(e).__name__}] {e}")
                continue

            if response["status"] == "clarification_needed":
                _print_clarification(response["questions"])
            else:
                _print_plan(response["plan"])

        except KeyboardInterrupt:
            print("\nForced shutdown detected. Closing...")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")


if __name__ == "__main__":
    main()

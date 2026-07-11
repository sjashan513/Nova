"""
core/cli/output.py — funciones de presentación para el CLI de Nova.

Responsabilidad única: formatear y imprimir output en el terminal.
Sin lógica de negocio, sin efectos secundarios salvo print().

Separado de cli.py para que el día que Nova tenga UI o API,
este módulo sea el único adapter que cambia — el loop no se toca.
"""

from core.domain.exceptions import PlanContractErrorGroup, RetriesExhaustedError

_CONTEXT_VALUE_TRUNCATE = 300


def print_plan(plan) -> None:
    print(f"\nObjective: {plan.objective}")
    print(f"Steps ({len(plan.steps)}):")
    for step in plan.steps:
        print(f"  [{step.id}] {step.tool_or_worker} -- {step.description}")
        if step.assumes:
            print(f"      assumes: {', '.join(step.assumes)}")


def print_clarification(questions) -> None:
    print("\nNova needs clarification before planning this task:")
    for i, q in enumerate(questions, start=1):
        print(f"  {i}. {q}")


def print_contract_error_group(e: PlanContractErrorGroup) -> None:
    print(
        f"\n[PlanContractErrorGroup] Could not produce a valid plan after "
        f"retrying with Kimi. {len(e.errors)} unresolved error(s):"
    )
    for err in e.errors:
        print(
            f"  - [{type(err).__name__}] step '{err.step_id}': '{err.raw_value}'")


def print_planner_error(e) -> None:
    print(f"\n[{type(e).__name__}] {e}")
    if e.raw_response:
        print(f"  raw_response: {e.raw_response!r}")


def print_execution_result(result: dict) -> None:
    print(
        f"\n[DONE] Plan ejecutado. {len(result['context'])} step(s) completados.")
    print("\nContexto por step:")
    for step_id, output in result["context"].items():
        print(f"  [{step_id}]:")
        if not output:
            print("    (sin output)")
            continue
        for key, value in output.items():
            raw = repr(value)
            truncated = raw[:_CONTEXT_VALUE_TRUNCATE]
            suffix = "..." if len(raw) > _CONTEXT_VALUE_TRUNCATE else ""
            print(f"    {key}: {truncated}{suffix}")


def print_retries_exhausted(e: RetriesExhaustedError) -> None:
    print(f"\n[RetriesExhaustedError] Step '{e.step_id}' falló tras "
          f"{len(e.attempts)} intento(s):")
    for attempt in e.attempts:
        print(f"  intento {attempt.attempt}: {type(attempt.original_error).__name__}: "
              f"{attempt.original_error}")


def print_assumes_failed(e) -> None:
    print(
        f"\n[PAUSED] El plan se pausó antes de ejecutar el step '{e.step_id}'.")
    print(f"  La(s) siguiente(s) precondición(es) no se cumplieron:\n")
    for f in e.failures:
        print(f"  [{f.op}] {f.reason}")
    print(f"\n  El plan fue generado contra un estado del mundo que ya no es válido.")
    print(f"  Opciones: corrige la precondición y reintenta la tarea, o cancela.")

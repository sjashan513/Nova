"""
Nova CLI — Fase 4: Planner + Director conectados de punta a punta.

Flujo completo:
    Nova> "arregla signal.ts"
      → Iniciador.get_plan()        (Kimi via NIM)
      → _print_plan()               (muestra el plan al usuario)
      → _confirm_execution()        (pregunta confirmación)
      → DirectorInstance.run()      (ejecuta el plan real)
      → _print_execution_result()   (muestra el contexto raw, truncado a 300 chars)

Cambios respecto a Fase 3:
  - Imports: DirectorInstance + RetriesExhaustedError añadidos.
  - _confirm_execution(): nueva función, pregunta [s/n] antes de ejecutar.
  - _execute_plan(): nueva función, instancia DirectorInstance y gestiona
    RetriesExhaustedError (el único error nuevo que puede salir del Director
    que el CLI no manejaba antes; PlanContractErrorGroup ya estaba).
  - _print_execution_result(): nueva función, itera el contexto por step,
    trunca cada value a 300 chars para que el terminal no explote con
    contenido de ficheros TypeScript completos.
  - _print_retries_exhausted(): nueva función, formatea RetriesExhaustedError
    mostrando cada intento con su error original.
  - main(): después de _print_plan, ahora llama _execute_plan si el usuario
    confirma. Sin confirmación, el plan se descarta sin ejecutar nada.

Nada más cambió. DirectorInstance, Iniciador, planner.py, models.py
están sellados y no se tocan.
"""

from core.planner.iniciador import Iniciador
from core.director.director_instance import DirectorInstance
from core.domain.exceptions import (
    PlanContractErrorGroup,
    PlannerError,
    PlanContractError,
    RetriesExhaustedError,
    AssumesFailedError,
)
from memory.wal.wal_reader import WALReader
from memory.bibliotecario import bibliotecario

# Values del contexto truncados a este límite -- suficiente para ver
# qué devolvió cada step sin que el terminal explote con el contenido
# completo de un fichero TypeScript de 500 líneas.
_CONTEXT_VALUE_TRUNCATE = 300


def _recover_wal() -> None:
    r = WALReader()
    pending = list(r.unprocessed())
    if pending:
        print(
            f"[WAL] {len(pending)} evento(s) no procesado(s) detectado(s) al arrancar:")
        for e in pending:
            print(f"  - {e['worker']} / {e['project']} / ts={e['ts']}")


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


def _confirm_execution() -> bool:
    """
    Pregunta confirmación antes de ejecutar el plan. Cualquier respuesta
    que no sea "s" (case-insensitive) descarta la ejecución sin tocar
    nada. Esto es el human-in-the-loop mínimo de Fase 4 -- en Fase 5
    (diff gate) el punto de confirmación se mueve al nivel del Step
    crítico, pero para el CLI de texto de esta fase, una confirmación
    por plan es suficiente.
    """
    try:
        answer = input("\n¿Ejecutar este plan? [s/n]: ").strip().lower()
        return answer == "s"
    except (EOFError, KeyboardInterrupt):
        # EOF en entornos no interactivos, o Ctrl+C durante la pregunta
        # -- tratar como "no" en ambos casos, nunca ejecutar por defecto.
        return False


def _print_execution_result(result: dict) -> None:
    """
    Imprime el contexto raw del Director, step por step, truncando cada
    value a _CONTEXT_VALUE_TRUNCATE chars. El contexto es un dict plano
    {step_id: {key: value}} donde cada value puede ser un str (contenido
    de fichero, stdout), un int (exit_code, bytes_written), o una lista
    (entries de directorio, args).

    repr() en lugar de str() para que los strings muestren sus comillas
    y los None/int se distingan visualmente de strings vacíos -- útil
    para debugging.
    """
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


def _print_retries_exhausted(e: RetriesExhaustedError) -> None:
    """
    Formatea RetriesExhaustedError mostrando cada intento con su error
    original. En el caso de WorkerExecutionError (short-circuit de Nivel 1),
    e.attempts tiene exactamente un elemento -- esto es correcto y esperado,
    no un bug (ver error_policy.py::execute_with_retry).
    """
    print(f"\n[RetriesExhaustedError] Step '{e.step_id}' falló tras "
          f"{len(e.attempts)} intento(s):")
    for attempt in e.attempts:
        print(f"  intento {attempt.attempt}: {type(attempt.original_error).__name__}: "
              f"{attempt.original_error}")


def _print_assumes_failed(e: AssumesFailedError) -> None:
    """
    Formatea AssumesFailedError mostrando qué precondición falló y por qué.
    PAUSED ≠ FAILED: nada se rompió, el mundo cambió desde la planificación.
    El usuario decide si reintentar (tras corregir la precondición), saltar
    el step, o abortar el plan completo.
    """
    print(
        f"\n[PAUSED] El plan se pausó antes de ejecutar el step '{e.step_id}'.")
    print(f"  La(s) siguiente(s) precondición(es) no se cumplieron:\n")
    for f in e.failures:
        print(f"  [{f.op}] {f.reason}")
    print(f"\n  El plan fue generado contra un estado del mundo que ya no es válido.")
    print(f"  Opciones: corrige la precondición y reintenta la tarea, o cancela.")


def _execute_plan(plan, registry: dict, projects: dict) -> None:
    """
    Instancia un DirectorInstance fresco para este plan y lo ejecuta.
    Un DirectorInstance por plan, nunca reutilizado -- ver
    director_instance.py's module docstring ("Una instancia, un Plan").

    Fase 5: recibe registry y projects para pasárselos al Director, que
    los inyecta en el Comparador antes de cada dispatch. Sin ellos el
    Comparador es un no-op (backwards compat con tests existentes).

    Errores manejados aquí:
      - AssumesFailedError: una precondición implícita falló antes de
        ejecutar un step. El plan quedó PAUSED. Nada se intentó, nada
        se rompió -- el mundo cambió desde la planificación.
      - RetriesExhaustedError: un step agotó su presupuesto de reintentos
        (Nivel 1 + Nivel 2 si había fallback_model). El plan quedó FAILED.
      - PlanAbortedError: Jashan rechazó un diff. El plan quedó ABORTED.
      - PlanContractErrorGroup: el DAG o las referencias de input son
        inválidas -- safety net, no debería ocurrir si el Iniciador hizo
        su trabajo.

    Cualquier otra excepción no capturada aquí sube al handler genérico
    del main loop (Exception) -- no se silencia nada.
    """
    from core.domain.exceptions import PlanAbortedError
    director = DirectorInstance(plan, registry=registry, projects=projects)
    try:
        result = director.run()
        _print_execution_result(result)
    except AssumesFailedError as e:
        _print_assumes_failed(e)
    except RetriesExhaustedError as e:
        _print_retries_exhausted(e)
    except PlanAbortedError as e:
        print(f"\n[ABORTED] Diff rechazado. El plan fue cancelado. {e}")
    except PlanContractErrorGroup as e:
        print(f"\n[PlanContractErrorGroup] El Director rechazó el plan "
              f"antes de ejecutar nada. {len(e.errors)} error(s):")
        _print_contract_error_group(e)


def main():
    print("Nova CLI v1.0 - Initialized (Phase 5: Comparador activo)")
    print("Type 'exit' or 'quit' to terminate.")
    _recover_wal()
    processed = bibliotecario.start()
    if processed:
        print(f"[Bibliotecario] {processed} evento(s) escritos en SQLite.")
    iniciador = Iniciador()

    # Cargamos registry y projects una sola vez al arrancar -- son
    # inmutables en runtime, no hay razón para recargarlos por plan.
    import yaml
    with open("registry/tool_registry.yaml", encoding="utf-8") as f:
        registry = yaml.safe_load(f)
    with open("registry/project_registry.yaml", encoding="utf-8") as f:
        projects = yaml.safe_load(f) or {}

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
                _print_contract_error_group(e)
                continue
            except PlannerError as e:
                _print_planner_error(e)
                continue
            except PlanContractError as e:
                print(f"\n[{type(e).__name__}] {e}")
                continue

            if response["status"] == "clarification_needed":
                _print_clarification(response["questions"])
                continue

            # status == "ready" -- mostrar el plan y pedir confirmación
            # antes de tocar cualquier fichero o llamar a cualquier worker.
            _print_plan(response["plan"])

            if _confirm_execution():
                _execute_plan(response["plan"],
                              registry=registry, projects=projects)
            else:
                print("Ejecución cancelada.")

        except KeyboardInterrupt:
            print("\nForced shutdown detected. Closing...")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")


if __name__ == "__main__":
    main()

"""
Nova CLI — FM4: capa conversacional Qwen/Groq con memory_tool y nova_plan.

Todo input pasa por Qwen primero. Qwen decide:
  - Conversación directa → content sin tool_calls
  - Necesita memoria     → tool_call: memory_tool
  - Tarea ejecutable     → tool_call: nova_plan (+ content de confirmación)
  - Cadena mixta         → memory_tool → nova_plan en rondas sucesivas

El loop de tool_calls corre hasta MAX_TOOL_ROUNDS o hasta que Qwen
no emita más tool_calls. nova_plan rompe el loop e invoca el Planner.
"""

import json
import re

from core.init.nova_boot import boot
from core.init.registry_loader import load_registries
from core.init.nova_init_helper import build_context
from core.planner.iniciador import Iniciador
from core.director.director_instance import DirectorInstance
from core.domain.exceptions import (
    PlanContractErrorGroup,
    PlannerError,
    PlanContractError,
    RetriesExhaustedError,
    AssumesFailedError,
    PlanAbortedError,
)
from core.llm.groq_client import call_groq
from core.cli.output import (
    print_plan,
    print_clarification,
    print_contract_error_group,
    print_planner_error,
    print_execution_result,
    print_retries_exhausted,
    print_assumes_failed,
)
from memory.tool.memory_tool import TOOLS, execute_memory

_MAX_TOOL_ROUNDS = 5


def _strip_markdown(text: str) -> str:
    """
    Elimina markdown del output antes de imprimirlo o enviarlo a TTS.
    Determinístico — no depende de que el modelo cumpla las instrucciones.
    """
    # Negritas y cursivas: **texto**, *texto*, __texto__, _texto_
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = re.sub(r'_{1,2}([^_]+)_{1,2}', r'\1', text)
    # Backticks inline y bloques de código
    text = re.sub(r'```[\w]*\n?', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Encabezados
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Listas numeradas: "1. foo" → "foo"
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Listas con viñetas: "- foo", "* foo", "+ foo"
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    # Líneas horizontales
    text = re.sub(r'^[-*_]{3,}$', '', text, flags=re.MULTILINE)
    # Múltiples líneas vacías → una sola
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _confirm_execution() -> bool:
    try:
        answer = input("\n¿Ejecutar este plan? [s/n]: ").strip().lower()
        return answer == "s"
    except (EOFError, KeyboardInterrupt):
        return False


def _execute_plan(plan, registry: dict, projects: dict) -> None:
    director = DirectorInstance(plan, registry=registry, projects=projects)
    try:
        result = director.run()
        print_execution_result(result)
    except AssumesFailedError as e:
        print_assumes_failed(e)
    except RetriesExhaustedError as e:
        print_retries_exhausted(e)
    except PlanAbortedError as e:
        print(f"\n[ABORTED] Diff rechazado. El plan fue cancelado. {e}")
    except PlanContractErrorGroup as e:
        print(f"\n[PlanContractErrorGroup] El Director rechazó el plan "
              f"antes de ejecutar nada. {len(e.errors)} error(s):")
        print_contract_error_group(e)


def _handle_nova_plan(task: str, iniciador: Iniciador, registry: dict, projects: dict) -> None:
    try:
        response = iniciador.get_plan(task)
    except PlanContractErrorGroup as e:
        print_contract_error_group(e)
        return
    except PlannerError as e:
        print_planner_error(e)
        return
    except PlanContractError as e:
        print(f"\n[{type(e).__name__}] {e}")
        return

    if response["status"] == "clarification_needed":
        print_clarification(response["questions"])
        return

    print_plan(response["plan"])

    if _confirm_execution():
        _execute_plan(response["plan"], registry=registry, projects=projects)
    else:
        print("Ejecución cancelada.")


def _chat(user_input: str, ctx, iniciador: Iniciador, registry: dict, projects: dict) -> None:
    ctx.add_user(user_input)

    for round_n in range(_MAX_TOOL_ROUNDS):
        message = call_groq(ctx.messages, tools=TOOLS)
        ctx.add_assistant(message)

        content = message.get("content") or ""
        tool_calls = message.get("tool_calls")

        print(
            f"[DEBUG] round {round_n + 1} — tools: {[tc['function']['name'] for tc in tool_calls] if tool_calls else None}")

        if content:
            print(f"\nNova: {_strip_markdown(content)}")
        elif not tool_calls:
            print("\nNova: (sin respuesta)")

        if not tool_calls:
            break

        nova_plan_call = None
        memory_calls = []

        for tc in tool_calls:
            name = tc["function"]["name"]
            if name == "nova_plan":
                nova_plan_call = tc
            else:
                memory_calls.append(tc)

        for tc in memory_calls:
            args = json.loads(tc["function"]["arguments"])
            result = execute_memory(
                archipelago=args["archipelago"],
                query=args["query"],
            )
            ctx.add_tool_result(tc["id"], result)

        if nova_plan_call:
            args = json.loads(nova_plan_call["function"]["arguments"])
            ctx.add_tool_result(
                nova_plan_call["id"], "Tarea recibida. Arrancando Planner.")
            _handle_nova_plan(args["task"], iniciador, registry, projects)
            break


def main():
    print("Nova CLI v1.0 - FM4 online")
    print("Type 'exit' or 'quit' to terminate.")

    boot()
    registry, projects = load_registries()
    ctx = build_context()
    iniciador = Iniciador()

    while True:
        try:
            user_input = input("\nNova> ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                print("Shutting down systems. See you next time!")
                break

            if user_input.lower() == "help":
                print("Commands:")
                print("  exit / quit — cierra Nova")
                print("  help        — este mensaje")
                print("  [texto]     — habla con Nova")
                continue

            _chat(user_input, ctx, iniciador, registry, projects)

        except KeyboardInterrupt:
            print("\nForced shutdown detected. Closing...")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")


if __name__ == "__main__":
    main()

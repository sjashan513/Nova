"""
Diagnóstico del Planner — imprime el JSON crudo que devuelve el LLM
antes de que el Iniciador lo valide o reintente.

Uso:
    python test_planner.py "arregla los errores en test_fixtures/pulse_sandbox/signal.ts"
"""

import json
import os
import sys
import re
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# Cambia esto al modelo que estés probando
NIM_MODEL = "z-ai/glm-5.2"
NIM_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
REQUEST_TIMEOUT_SECONDS = 120


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1) if match else text


def call_raw(task: str, retry_context: Optional[str] = None) -> str:
    """Llama al LLM y devuelve el texto crudo sin parsear nada."""
    api_key = os.environ.get("NIM_API_KEY")
    if not api_key:
        raise RuntimeError("NIM_API_KEY no está en .env")

    # Importamos el system prompt real para que las condiciones sean idénticas
    # a una llamada real del Iniciador
    from core.planner.planner_prompt import build_system_prompt

    user_content = task
    if retry_context:
        user_content = f"{task}\n\n--- CORRECTION NEEDED ---\n{retry_context}"

    payload = {
        "model": NIM_MODEL,
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
    }

    print(f"[→] Llamando a {NIM_MODEL}...")
    response = requests.post(
        NIM_ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    raw_body = response.json()
    return raw_body["choices"][0]["message"]["content"]


def main():
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else \
        "arregla los errores en test_fixtures/pulse_sandbox/signal.ts"

    print(f"Task: {task}")
    print("-" * 60)

    # --- Intento 1: sin retry context ---
    raw = call_raw(task)
    print("\n[RAW RESPONSE — intento 1]")
    print(raw)
    print("-" * 60)

    cleaned = _strip_markdown_fences(raw)
    print("\n[CLEANED (sin fences)]")
    print(cleaned)
    print("-" * 60)

    try:
        data = json.loads(cleaned)
        print("\n[PARSED JSON — intento 1]")
        print(json.dumps(data, indent=2, ensure_ascii=False))

        # Si el plan existe, mostrar cada step con sus campos relevantes
        if data.get("status") == "ready" and data.get("plan"):
            print("\n[STEPS — detalle]")
            for step in data["plan"].get("steps", []):
                print(f"  [{step.get('id')}] {step.get('tool_or_worker')}")
                print(f"    action: {step.get('action')!r}")
                print(f"    model: {step.get('model')!r}")
                print(f"    input: {step.get('input')}")
                print(f"    depends_on: {step.get('depends_on')}")

    except json.JSONDecodeError as e:
        print(f"\n[ERROR] No es JSON válido: {e}")


if __name__ == "__main__":
    main()

"""
core/session/context.py — historial de sesión en RAM para la capa conversacional.

Context acumula el historial de mensajes de la sesión actual y lo expone
como List[dict] lista para pasar directamente a groq_client.call_groq().

Ciclo de vida: una instancia por sesión de cli.py. Muere al cerrar Nova.
No persiste nada — la persistencia es responsabilidad del Bibliotecario y Zep.

Mensajes soportados:
  system       → system prompt de Nova, añadido una vez al instanciar
  user         → input de Jashan
  assistant    → respuesta de Qwen (con o sin tool_calls)
  tool         → resultado de ejecutar una tool (memory_tool, etc.)

El historial se pasa íntegro a Groq en cada llamada — Groq no tiene
memoria entre requests, el caller es responsable de la continuidad.
"""

from typing import Any, Dict, List, Optional

_NOVA_SYSTEM_PROMPT = """/no_think

Eres Nova, el asistente personal de Jashan. Directo, técnico, sin disclaimers.
Responde siempre en el idioma en que Jashan escribe.
Cuando necesites contexto de sesiones anteriores, usa la tool memory_tool.
Nunca inventes información que deberías buscar en memoria."""


class Context:
    """
    Gestiona el historial de mensajes de la sesión actual en RAM.

    Uso típico en cli.py:
        ctx = Context()
        ctx.add_user("arregla signal.ts")
        message = call_groq(ctx.messages, tools=[SCHEMA])
        ctx.add_assistant(message)
        # si hay tool_call:
        ctx.add_tool_result(tool_call_id, result_text)
        message2 = call_groq(ctx.messages, tools=[SCHEMA])
        ctx.add_assistant(message2)
    """

    def __init__(self, system_prompt: str = _NOVA_SYSTEM_PROMPT) -> None:
        self._messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

    @property
    def messages(self) -> List[Dict[str, Any]]:
        """
        Devuelve el historial completo listo para pasar a call_groq().
        La lista es una copia superficial — el caller no puede mutar
        el historial interno accidentalmente.
        """
        return list(self._messages)

    def add_user(self, content: str) -> None:
        """Añade un mensaje de Jashan al historial."""
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, message: Dict[str, Any]) -> None:
        """
        Añade la respuesta de Qwen al historial.

        message es el dict crudo de choices[0].message devuelto por
        call_groq() — puede tener content, tool_calls, o ambos.
        Groq requiere que el mensaje del assistant con tool_calls
        se incluya íntegro en el historial antes del tool result.
        """
        self._messages.append({"role": "assistant", **message})

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """
        Añade el resultado de una tool call al historial.

        tool_call_id debe coincidir con el id del tool_call emitido
        por Qwen — Groq lo usa para correlacionar llamada y resultado.
        content es el texto plano devuelto por memory_tool.execute().
        """
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def reset(self) -> None:
        """
        Reinicia el historial manteniendo el system prompt.
        Útil para empezar una nueva conversación sin recrear la instancia.
        """
        system = self._messages[0]
        self._messages = [system]

"""
Extractor — WAL event → structured memory for Zep.

Calls GPT-4o-mini with the raw WAL event and asks for a natural-language
description + metadata dict. The prompt is intentionally generic — the
Extractor has no knowledge of which workers exist. If a new worker is
added to the registry tomorrow, this file does not change.

Uses call_openai() from core/llm/openai_client.py — same pattern as
every other LLM caller in Nova. No direct OpenAI SDK usage here.

Returns None on any failure — the Bibliotecario skips the event and
leaves it unprocessed so the next poll cycle retries it.
"""

import json
import logging
from typing import Optional

from core.llm.openai_client import call_openai
from core.llm.nim_client import parse_json_response

logger = logging.getLogger(__name__)

_MODEL = "gpt-5.4-nano"

_SYSTEM_PROMPT = """\
You are a memory extraction assistant for an AI development tool called Nova.
You receive a raw JSON event describing something that happened during a coding session.
Your job is to produce a structured memory entry that can be stored in a knowledge graph.

Return ONLY valid JSON with this exact shape:
{
  "content": "<natural language description of what happened, 1-3 sentences, in English>",
  "metadata": {
    "worker": "<worker name from the event>",
    "project": "<project name>",
    "branch": "<branch name>",
    "ts": <unix timestamp as integer>
  }
}

Rules:
- content must be in English, factual, no opinions
- include all technically relevant details (error types, file names, change descriptions)
- metadata must contain exactly the four keys above, no more
- return ONLY the JSON object, no markdown, no preamble
"""


class Extractor:
    def extract(self, event: dict) -> Optional[dict]:
        """
        Extracts structured memory from a WAL event.

        Returns a dict ready for zep_client.add_memory():
          {"messages": [{"role": "system", "content": ..., "metadata": {...}}]}

        Returns None if extraction fails — Bibliotecario skips and retries
        on the next poll cycle.
        """
        try:
            raw = call_openai(
                model=_MODEL,
                system_prompt=_SYSTEM_PROMPT,
                user_content=json.dumps(event, ensure_ascii=False),
                temperature=0.0,       # extraction, not creativity
                max_tokens=300,
                response_format="json_object",  # native JSON mode — guaranteed valid JSON
            )

            parsed = parse_json_response(
                raw, required_keys=["content", "metadata"])

            return {
                "messages": [
                    {
                        "role": "system",
                        "content": parsed["content"],
                        "metadata": parsed["metadata"],
                    }
                ]
            }

        except Exception as e:
            logger.error(
                "[Extractor] failed to extract event %s: %s",
                event.get("event_id", "unknown"),
                e,
            )
            return None

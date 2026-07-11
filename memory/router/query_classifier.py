"""
QueryClassifier — determina el tipo de query para el Memory Router.

Clasificación determinística por patrones regex. Sin LLM.

Tres resultados posibles:
  "episodic"   → Zep (decisiones pasadas, historial, errores, causalidad)
  "structural" → codebase-mcp (estado actual del código, símbolos, schema)
  "ambiguous"  → ambos motores en paralelo, resultados combinados

Reglas de clasificación:
  - Si solo structural matchea → "structural"
  - Si solo episodic matchea   → "episodic"
  - Si ambos matchean          → "ambiguous"
  - Si ninguno matchea         → "ambiguous" (default seguro: consulta ambos)
"""

import re

_STRUCTURAL_PATTERNS = [
    # "qué workers/funciones/clases existen/hay" — singular y plural
    r"\b(qué|cuáles|cuales|lista|dame|muestra|hay)\b.{0,40}\b(workers?|tools?|funciones?|clases?|módulos?|imports?|ficheros?|archivos?|schemas?|registros?|entries|entry)\b",
    # "qué X existen en Nova/Pulse" — sujeto primero
    r"\b(workers?|tools?|funciones?|clases?)\b.{0,20}\b(existen?|hay|registrados?|disponibles?)\b",
    # "dónde se usa / está / define / importa X"
    r"\b(dónde|donde)\b.{0,20}\b(usa|está|define|importa|aparece)\b",
    # "existe/existen X en el código"
    r"\b(existe|existen|hay)\b.{0,20}\b(workers?|clases?|funciones?|ficheros?|archivos?)\b",
    # términos de estructura explícitos
    r"\b(schema|estructura|firma|signatura|interface|type)\b",
    # "qué imports necesita X"
    r"\b(imports?|dependencias)\b.{0,20}\b(necesita|tiene|usa)\b",
    # "muéstrame / lista / dame los workers"
    r"\b(muéstrame|muestrame|lista|dame|enumera)\b.{0,30}\b(workers?|tools?|clases?|funciones?)\b",
]

_EPISODIC_PATTERNS = [
    # causalidad y decisiones
    r"\b(por qué|porque|cómo decidimos|elegimos|elegiste|decidimos|razón)\b",
    # tiempo y historial
    r"\b(cuándo|cuando|último|última|antes|pasado|ayer|semana|historial|sesión anterior)\b",
    # errores pasados
    r"\b(error|errores|falló|fallo|fix|arreglamos)\b.{0,30}\b(signal|fichero|archivo|en|hace)\b",
    # "qué pasó / qué hicimos / qué cambió"
    r"\b(qué pasó|qué hicimos|qué cambió|qué añadimos|cuántas tareas)\b",
    # memoria explícita
    r"\b(recuerdas|recuerda|sabes|sabías|en la sesión)\b",
]


def classify(query: str) -> str:
    """
    Returns "episodic", "structural", or "ambiguous".
    """
    q = query.lower()

    structural = any(re.search(p, q) for p in _STRUCTURAL_PATTERNS)
    episodic = any(re.search(p, q) for p in _EPISODIC_PATTERNS)

    if structural and not episodic:
        return "structural"
    if episodic and not structural:
        return "episodic"

    # Both matched, or neither matched — consult both engines.
    return "ambiguous"

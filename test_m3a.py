"""
test_m3b.py — Test de cierre M3b

Verifica el query path estructural completo:
  query → IslandResolver → QueryClassifier → MemoryRouter → codebase-mcp → resultado

Ejecutar desde la raíz de Nova:
  python test_m3b.py

Prerequisito: indexer.index_island() ejecutado al menos una vez.
"""

from memory.router.memory_router import MemoryRouter
from memory.codebase_mcp.query_engine import QueryEngine
from memory.zep.zep_client import ZepClient
import os
import sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def test_structural():
    print("\n=== M3b — Query estructural ===")
    query = "qué workers existen en Nova"
    print(f"Query: '{query}'")

    zep = ZepClient(api_key=os.environ["ZEP_API_KEY"])
    router = MemoryRouter(zep=zep, query_engine=QueryEngine())

    result = router.query(archipelago="dev", query=query)

    print(f"Island:  {result['island']}")
    print(f"Type:    {result['type']}")
    print(f"Results: {len(result['results'])} encontrado(s)")

    for i, r in enumerate(result["results"]):
        print(f"\n  [{i+1}] {r}")

    assert result["type"] in ("structural", "ambiguous"), \
        f"Expected structural or ambiguous, got {result['type']}"
    assert len(result["results"]) > 0, \
        "Expected at least one result from codebase-mcp — did indexer run?"

    print("\n✅ M3b PASS — query path estructural funciona")


if __name__ == "__main__":
    try:
        test_structural()
    except AssertionError as e:
        print(f"\n❌ M3b FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ M3b ERROR: {e}")
        raise

"""
test_check.py — diagnóstico directo de worker_ts_check.

Apunta a test_fixtures/pulse_sandbox/signal.ts y imprime
el output raw del worker para ver exactamente qué errores detecta.

Uso:
    cd ~/Desktop/nova
    python test_check.py
"""

import json
import os
import subprocess
from workers.coding.worker_ts_check import WorkerTsCheck, _parse_tsc_output
from registry.project_registry import get_project

# --- Diagnóstico raw de tsc ---
project = get_project("PulseSandbox")
project_path = project["path"]
file_path = "test_fixtures/pulse_sandbox/signal.ts"

print(f"project_path: {project_path}")
print(f"file_path input: {file_path}")

abs_file = os.path.normpath(
    os.path.join(project_path, file_path)
    if not os.path.isabs(file_path)
    else file_path
)
print(f"abs_file resuelto: {abs_file}")
print()

# Correr tsc directamente
completed = subprocess.run(
    ["npx", "tsc", "--noEmit", "--pretty", "false"],
    cwd=project_path,
    capture_output=True,
    text=True,
    timeout=120,
)

print(f"tsc returncode: {completed.returncode}")
print(f"tsc stdout (primeras 20 líneas):")
for line in completed.stdout.splitlines()[:20]:
    print(f"  {line}")
print()

# Ver qué paths emite tsc
errors_raw = _parse_tsc_output(completed.stdout)
print(f"Total errores parseados (sin filtro): {len(errors_raw)}")
print("Paths únicos que emite tsc:")
for p in sorted(set(e["file"] for e in errors_raw)):
    normalized = os.path.normpath(os.path.join(project_path, p))
    match = "✓ MATCH" if normalized == abs_file else "✗"
    print(f"  {match}  tsc='{p}'  →  norm='{normalized}'")
print()

# --- Worker completo ---
worker = WorkerTsCheck()
result = worker.execute({
    "project": "PulseSandbox",
    "file_path": "/home/uzux/Desktop/nova/test_fixtures/pulse_sandbox/signal.ts",
})

print(f"status: {result['status']}")
print(f"reason: {result['reason']}")
print(f"result raw: {result['result']}")

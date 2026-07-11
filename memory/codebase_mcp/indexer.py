"""
Indexer — tree-sitter structural indexer for Nova and Pulse repos.

Indexes exported symbols (functions, classes), imports between modules,
and registry entries (tool_registry.yaml, project_registry.yaml) into
a SQLite database (index.db) for fast structural queries.

Incremental: only re-indexes files whose mtime has changed since the
last index run. A full re-index is triggered on first run or if index.db
is deleted.

Languages supported:
  TypeScript (.ts, .tsx) — tree-sitter-typescript
  Python (.py)           — tree-sitter-python
  YAML (.yaml)           — parsed directly (PyYAML), not tree-sitter

Triggered by:
  - Nova startup (if any repo file has changed since last index)
  - After any filesystem.write or vscode.show_diff accepted step
    (caller responsibility — the Indexer just re-indexes what changed)
"""

import hashlib
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)

_INDEX_DB_PATH = Path(__file__).parent / "index.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    island      TEXT    NOT NULL,
    path        TEXT    NOT NULL UNIQUE,
    mtime       REAL    NOT NULL,
    indexed_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    island      TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL,   -- "function" | "class" | "variable" | "type"
    line        INTEGER NOT NULL,
    exported    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    island      TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    imported_from TEXT  NOT NULL,   -- module path as written in the source
    names       TEXT    NOT NULL    -- JSON array of imported names
);

CREATE TABLE IF NOT EXISTS registry_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    island      TEXT    NOT NULL,
    registry    TEXT    NOT NULL,   -- "tool_registry" | "project_registry"
    entry_name  TEXT    NOT NULL,
    entry_data  TEXT    NOT NULL    -- JSON-serialised entry dict
);

CREATE INDEX IF NOT EXISTS idx_symbols_island     ON symbols(island);
CREATE INDEX IF NOT EXISTS idx_symbols_name       ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file       ON symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_imports_island     ON imports(island);
CREATE INDEX IF NOT EXISTS idx_registry_island    ON registry_entries(island);
CREATE INDEX IF NOT EXISTS idx_registry_name      ON registry_entries(entry_name);
"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_INDEX_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Tree-sitter parsers — lazy loaded per language
# ---------------------------------------------------------------------------

_ts_parsers = {}


def _get_ts_parser(language: str):
    """
    Returns a tree-sitter Parser for the given language.
    Lazy-loads the language grammar on first call.
    """
    if language in _ts_parsers:
        return _ts_parsers[language]

    try:
        import tree_sitter_python as tspython
        import tree_sitter_typescript as tstypescript
        from tree_sitter import Language, Parser

        if language == "python":
            lang = Language(tspython.language())
        elif language == "typescript":
            lang = Language(tstypescript.language_typescript())
        elif language == "tsx":
            lang = Language(tstypescript.language_tsx())
        else:
            return None

        parser = Parser(lang)
        _ts_parsers[language] = parser
        return parser
    except Exception as e:
        logger.warning(
            "[Indexer] tree-sitter parser unavailable for %s: %s", language, e)
        return None


# ---------------------------------------------------------------------------
# Symbol extraction helpers
# ---------------------------------------------------------------------------

def _extract_python_symbols(source: bytes, file_path: str) -> List[dict]:
    """
    Extracts exported (module-level) functions and classes from Python source.
    'Exported' in Python = defined at module level (not inside another function/class).
    """
    parser = _get_ts_parser("python")
    if parser is None:
        return []

    tree = parser.parse(source)
    symbols = []

    def visit(node, depth=0):
        if depth == 0 and node.type in ("function_definition", "async_function_definition"):
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "name": name_node.text.decode("utf-8"),
                    "kind": "function",
                    "line": node.start_point[0] + 1,
                    "exported": 1,
                })
        elif depth == 0 and node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "name": name_node.text.decode("utf-8"),
                    "kind": "class",
                    "line": node.start_point[0] + 1,
                    "exported": 1,
                })
        for child in node.children:
            visit(child, depth + 1 if node.type in ("module",) else depth + 1)

    # Only visit top-level children
    for child in tree.root_node.children:
        visit(child, depth=0)

    return symbols


def _extract_typescript_symbols(source: bytes, file_path: str, tsx: bool = False) -> List[dict]:
    """
    Extracts exported functions, classes, and type aliases from TypeScript source.
    Only top-level exported declarations.
    """
    lang_key = "tsx" if tsx else "typescript"
    parser = _get_ts_parser(lang_key)
    if parser is None:
        return []

    tree = parser.parse(source)
    symbols = []

    export_node_types = {
        "export_statement",
        "export_default_declaration",
    }

    def extract_from_declaration(node, line):
        for child in node.children:
            if child.type in ("function_declaration", "function"):
                name = child.child_by_field_name("name")
                if name:
                    symbols.append({"name": name.text.decode(
                        "utf-8"), "kind": "function", "line": line, "exported": 1})
            elif child.type == "class_declaration":
                name = child.child_by_field_name("name")
                if name:
                    symbols.append({"name": name.text.decode(
                        "utf-8"), "kind": "class", "line": line, "exported": 1})
            elif child.type in ("type_alias_declaration", "interface_declaration"):
                name = child.child_by_field_name("name")
                if name:
                    symbols.append({"name": name.text.decode(
                        "utf-8"), "kind": "type", "line": line, "exported": 1})
            elif child.type == "lexical_declaration":
                for var in child.children:
                    if var.type == "variable_declarator":
                        name = var.child_by_field_name("name")
                        if name:
                            symbols.append({"name": name.text.decode(
                                "utf-8"), "kind": "variable", "line": line, "exported": 1})

    for child in tree.root_node.children:
        if child.type in export_node_types:
            extract_from_declaration(child, child.start_point[0] + 1)

    return symbols


def _extract_python_imports(source: bytes, file_path: str) -> List[dict]:
    parser = _get_ts_parser("python")
    if parser is None:
        return []

    import json
    tree = parser.parse(source)
    imports = []

    for child in tree.root_node.children:
        if child.type == "import_statement":
            names = [n.text.decode("utf-8")
                     for n in child.children if n.type == "dotted_name"]
            if names:
                imports.append(
                    {"imported_from": names[0], "names": json.dumps(names)})
        elif child.type == "import_from_statement":
            module = child.child_by_field_name("module_name")
            imported_names = [
                n.text.decode("utf-8")
                for n in child.children
                if n.type in ("dotted_name", "identifier") and n != module
            ]
            if module:
                imports.append({
                    "imported_from": module.text.decode("utf-8"),
                    "names": json.dumps(imported_names),
                })

    return imports


# ---------------------------------------------------------------------------
# File indexing
# ---------------------------------------------------------------------------

def _index_file(conn: sqlite3.Connection, island: str, file_path: Path) -> None:
    import json

    mtime = file_path.stat().st_mtime
    rel_path = str(file_path)
    source = file_path.read_bytes()

    suffix = file_path.suffix.lower()

    if suffix == ".py":
        symbols = _extract_python_symbols(source, rel_path)
        imports = _extract_python_imports(source, rel_path)
    elif suffix == ".ts":
        symbols = _extract_typescript_symbols(source, rel_path, tsx=False)
        imports = []  # TypeScript imports — future enhancement
    elif suffix == ".tsx":
        symbols = _extract_typescript_symbols(source, rel_path, tsx=True)
        imports = []
    else:
        symbols = []
        imports = []

    # Delete stale entries for this file
    conn.execute("DELETE FROM symbols WHERE file_path = ?", (rel_path,))
    conn.execute("DELETE FROM imports WHERE file_path = ?", (rel_path,))

    for sym in symbols:
        conn.execute(
            "INSERT INTO symbols (island, file_path, name, kind, line, exported) VALUES (?,?,?,?,?,?)",
            (island, rel_path, sym["name"],
             sym["kind"], sym["line"], sym["exported"]),
        )

    for imp in imports:
        conn.execute(
            "INSERT INTO imports (island, file_path, imported_from, names) VALUES (?,?,?,?)",
            (island, rel_path, imp["imported_from"], imp["names"]),
        )

    conn.execute(
        """INSERT INTO files (island, path, mtime, indexed_at)
           VALUES (?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, indexed_at=excluded.indexed_at""",
        (island, rel_path, mtime, int(time.time())),
    )


def _index_registry(conn: sqlite3.Connection, island: str, registry_path: Path) -> None:
    """
    Indexes tool_registry.yaml and project_registry.yaml entries.
    Re-indexes the full file every time (registries are small).
    """
    import json

    if not registry_path.exists():
        return

    registry_name = registry_path.stem  # "tool_registry" | "project_registry"

    with registry_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    conn.execute(
        "DELETE FROM registry_entries WHERE island = ? AND registry = ?",
        (island, registry_name),
    )

    if registry_name == "tool_registry":
        for section in ("tools", "workers"):
            for entry in data.get(section, []):
                conn.execute(
                    "INSERT INTO registry_entries (island, registry, entry_name, entry_data) VALUES (?,?,?,?)",
                    (island, registry_name, entry["name"], json.dumps(entry)),
                )
    elif registry_name == "project_registry":
        for project_name, project_data in data.items():
            conn.execute(
                "INSERT INTO registry_entries (island, registry, entry_name, entry_data) VALUES (?,?,?,?)",
                (island, registry_name, project_name, json.dumps(project_data)),
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_INDEXABLE_EXTENSIONS = {".py", ".ts", ".tsx"}
_SKIP_DIRS = {"node_modules", ".git", "__pycache__",
              ".venv", "venv", "dist", "build", "out"}


def index_island(island_name: str, repo_path: str, force: bool = False) -> int:
    """
    Indexes or incrementally updates the index for one island.

    force=True re-indexes all files regardless of mtime.
    Returns the number of files indexed.
    """
    conn = _get_conn()
    root = Path(repo_path)

    if not root.exists():
        logger.warning("[Indexer] repo path does not exist: %s", repo_path)
        conn.close()
        return 0

    # Load existing mtime cache
    mtime_cache = {}
    if not force:
        for row in conn.execute("SELECT path, mtime FROM files WHERE island = ?", (island_name,)):
            mtime_cache[row["path"]] = row["mtime"]

    indexed = 0

    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        if any(skip in file_path.parts for skip in _SKIP_DIRS):
            continue
        if file_path.suffix.lower() not in _INDEXABLE_EXTENSIONS:
            continue

        rel = str(file_path)
        current_mtime = file_path.stat().st_mtime

        if not force and mtime_cache.get(rel) == current_mtime:
            continue  # unchanged — skip

        try:
            _index_file(conn, island_name, file_path)
            indexed += 1
        except Exception as e:
            logger.warning("[Indexer] failed to index %s: %s", file_path, e)

    # Always re-index registries (they're small and change often)
    for reg_name in ("tool_registry.yaml", "project_registry.yaml"):
        reg_path = root / "registry" / reg_name
        try:
            _index_registry(conn, island_name, reg_path)
        except Exception as e:
            logger.warning(
                "[Indexer] failed to index registry %s: %s", reg_path, e)

    conn.commit()
    conn.close()

    logger.info("[Indexer] island='%s' indexed %d file(s)",
                island_name, indexed)
    return indexed

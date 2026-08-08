"""Parse a Python repository into a symbol and relationship graph."""

from __future__ import annotations

import ast
import builtins
import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .NodeWalker import NodeWalk, ParsedImport, ParsedReference, ParsedSymbol


BUILTIN_NAMES = frozenset(dir(builtins))


@dataclass(frozen=True)
class ParsedFile:
    path: Path
    source: str
    tree: ast.Module
    digest: str
    walker: NodeWalk


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_schema(connection: sqlite3.Connection) -> None:
    """Create the current schema and replace the incompatible legacy edge table."""

    existing_edge_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(edge_table)")
    }
    if existing_edge_columns and "resolution_status" not in existing_edge_columns:
        connection.execute("DROP TABLE edge_table")

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS repo_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS file_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            FOREIGN KEY (repo_id) REFERENCES repo_table(id) ON DELETE CASCADE,
            UNIQUE(repo_id, file_name)
        );
        CREATE TABLE IF NOT EXISTS raw_text_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL UNIQUE,
            hash_code TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            FOREIGN KEY (file_id) REFERENCES file_table(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS symbol_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            symbol_type TEXT NOT NULL,
            parent_symbol_id INTEGER,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            signature TEXT,
            docstring TEXT,
            decorators TEXT,
            FOREIGN KEY (file_id) REFERENCES file_table(id) ON DELETE CASCADE,
            FOREIGN KEY (parent_symbol_id) REFERENCES symbol_table(id) ON DELETE CASCADE,
            UNIQUE(file_id, qualified_name)
        );
        CREATE TABLE IF NOT EXISTS import_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            module TEXT,
            imported_name TEXT,
            local_name TEXT NOT NULL,
            line_number INTEGER NOT NULL,
            FOREIGN KEY (file_id) REFERENCES file_table(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS edge_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL,
            source_symbol_id INTEGER NOT NULL,
            target_symbol_id INTEGER,
            relationship_type TEXT NOT NULL,
            target_expression TEXT,
            target_name TEXT,
            line_number INTEGER,
            resolution_status TEXT NOT NULL,
            FOREIGN KEY (repo_id) REFERENCES repo_table(id) ON DELETE CASCADE,
            FOREIGN KEY (source_symbol_id) REFERENCES symbol_table(id) ON DELETE CASCADE,
            FOREIGN KEY (target_symbol_id) REFERENCES symbol_table(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_file_repo ON file_table(repo_id);
        CREATE INDEX IF NOT EXISTS idx_symbol_file ON symbol_table(file_id);
        CREATE INDEX IF NOT EXISTS idx_symbol_name ON symbol_table(name);
        CREATE INDEX IF NOT EXISTS idx_symbol_qualified_name ON symbol_table(qualified_name);
        CREATE INDEX IF NOT EXISTS idx_edge_source ON edge_table(source_symbol_id);
        CREATE INDEX IF NOT EXISTS idx_edge_target ON edge_table(target_symbol_id);
        CREATE INDEX IF NOT EXISTS idx_edge_repo ON edge_table(repo_id);
        """
    )


def _parseable_files(repository_path: Path) -> tuple[list[ParsedFile], list[str]]:
    parsed_files: list[ParsedFile] = []
    skipped: list[str] = []
    for path in sorted(repository_path.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            digest = _sha256(path)
            relative_path = str(path.relative_to(repository_path))
            walker = NodeWalk(relative_path, digest, source)
            walker.visit(tree)
            parsed_files.append(ParsedFile(path, source, tree, digest, walker))
        except (OSError, UnicodeError, SyntaxError) as exc:
            skipped.append(f"{path.relative_to(repository_path)} ({type(exc).__name__})")
    return parsed_files, skipped


def _delete_repository(connection: sqlite3.Connection, repo_id: int) -> None:
    # Explicit deletes also support databases created before ON DELETE CASCADE.
    file_ids = [
        row[0]
        for row in connection.execute(
            "SELECT id FROM file_table WHERE repo_id = ?", (repo_id,)
        )
    ]
    connection.execute("DELETE FROM edge_table WHERE repo_id = ?", (repo_id,))
    if file_ids:
        placeholders = ",".join("?" for _ in file_ids)
        connection.execute(
            f"DELETE FROM import_table WHERE file_id IN ({placeholders})", file_ids
        )
        connection.execute(
            f"DELETE FROM symbol_table WHERE file_id IN ({placeholders})", file_ids
        )
        connection.execute(
            f"DELETE FROM raw_text_table WHERE file_id IN ({placeholders})", file_ids
        )
        # Remove records from the retired schema when it is still present.
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='function_table'"
        ).fetchone():
            connection.execute(
                f"DELETE FROM function_table WHERE file_id IN ({placeholders})", file_ids
            )
    connection.execute("DELETE FROM file_table WHERE repo_id = ?", (repo_id,))
    connection.execute("DELETE FROM repo_table WHERE id = ?", (repo_id,))


def _insert_symbol(
    connection: sqlite3.Connection,
    file_id: int,
    symbol: ParsedSymbol,
    parent_symbol_id: int | None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO symbol_table (
            file_id, name, qualified_name, symbol_type, parent_symbol_id,
            line_start, line_end, signature, docstring, decorators
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file_id,
            symbol.name,
            symbol.qualified_name,
            symbol.symbol_type,
            parent_symbol_id,
            symbol.line_start,
            symbol.line_end,
            symbol.signature,
            symbol.docstring,
            "\n".join(symbol.decorators),
        ),
    )
    return int(cursor.lastrowid)


def _class_owner(symbol: ParsedSymbol, symbols: dict[str, ParsedSymbol]) -> str | None:
    parent_name = symbol.parent_qualified_name
    while parent_name:
        parent = symbols.get(parent_name)
        if parent is None:
            return None
        if parent.symbol_type == "class":
            return parent.qualified_name
        parent_name = parent.parent_qualified_name
    return None


def _resolve_reference(
    reference: ParsedReference,
    source_file: str,
    symbols_by_file: dict[str, dict[str, ParsedSymbol]],
    ids_by_file: dict[str, dict[str, int]],
    symbols_by_name: dict[str, list[tuple[str, ParsedSymbol, int]]],
    imports_by_file: dict[str, dict[str, ParsedImport]],
    repository_modules: set[str],
) -> tuple[int | None, str, str]:
    """Return target id, resolution status, and final relationship type."""

    local_symbols = symbols_by_file[source_file]
    local_ids = ids_by_file[source_file]
    source = local_symbols[reference.source_qualified_name]
    candidates: list[int] = []
    imported = imports_by_file[source_file].get(
        reference.target_expression.split(".", 1)[0]
    )

    if imported is not None:
        target_module = imported.module or ""
        if imported.imported_name and "." in reference.target_expression:
            target_module = ".".join(
                part for part in (target_module, imported.imported_name) if part
            )
        imported_target_name = (
            reference.target_name
            if "." in reference.target_expression
            else imported.imported_name or reference.target_name
        )
        module_path = target_module.lstrip(".").replace(".", "/")
        candidates = [
            symbol_id
            for file_name, symbol, symbol_id in symbols_by_name.get(
                imported_target_name, []
            )
            if not module_path
            or file_name.removesuffix(".py").replace("\\", "/") == module_path
            or file_name.replace("\\", "/").endswith(f"/{module_path}.py")
            or file_name.replace("\\", "/").endswith(f"/{module_path}/__init__.py")
        ]

    if not candidates and reference.relationship_type == "inherits":
        candidates = [
            symbol_id
            for _file, symbol, symbol_id in symbols_by_name.get(reference.target_name, [])
            if symbol.symbol_type == "class"
        ]
    elif not candidates and reference.target_expression.startswith(("self.", "cls.")):
        owner = _class_owner(source, local_symbols)
        if owner:
            target_qualified_name = f"{owner}.{reference.target_name}"
            target_id = local_ids.get(target_qualified_name)
            if target_id is not None:
                candidates = [target_id]
    elif not candidates and "." in reference.target_expression:
        # Resolve explicit Class.method expressions before falling back to a name.
        expression = reference.target_expression
        candidates = [
            symbol_id
            for _file, symbol, symbol_id in symbols_by_name.get(reference.target_name, [])
            if symbol.qualified_name == expression
            or symbol.qualified_name.endswith(f".{expression}")
        ]
    elif not candidates:
        parent = source.parent_qualified_name
        while parent:
            scoped_name = f"{parent}.{reference.target_name}"
            if scoped_name in local_ids:
                candidates = [local_ids[scoped_name]]
                break
            parent_symbol = local_symbols.get(parent)
            parent = parent_symbol.parent_qualified_name if parent_symbol else None

        if not candidates and reference.target_name in local_ids:
            candidates = [local_ids[reference.target_name]]

    if not candidates and imported is None:
        candidates = [
            symbol_id
            for _file, _symbol, symbol_id in symbols_by_name.get(
                reference.target_name, []
            )
        ]

    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        imported_module_is_local = (
            imported is not None and module_path in repository_modules
        )
        if not candidates and (
            (imported is not None and not imported_module_is_local)
            or reference.target_name in BUILTIN_NAMES
        ):
            return None, "external", reference.relationship_type
        return None, "ambiguous" if candidates else "unresolved", reference.relationship_type

    target_id = candidates[0]
    target_symbol = next(
        symbol
        for entries in symbols_by_name.values()
        for _file, symbol, symbol_id in entries
        if symbol_id == target_id
    )
    relationship = reference.relationship_type
    if relationship == "calls" and target_symbol.symbol_type == "class":
        relationship = "instantiates"
    return target_id, "resolved", relationship


def parse_repository(
    repository_path: Path,
    database_path: Path,
    *,
    replace_existing: bool = False,
) -> str:
    """Insert files, symbols, imports, and graph relationships transactionally."""

    repository_path = repository_path.resolve()
    parsed_files, skipped = _parseable_files(repository_path)

    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        duplicate = connection.execute(
            "SELECT id FROM repo_table WHERE path = ?", (str(repository_path),)
        ).fetchone()
        if duplicate:
            if not replace_existing:
                raise ValueError(
                    "This repository has already been parsed into the database."
                )
            _delete_repository(connection, duplicate[0])

        cursor = connection.execute(
            "INSERT INTO repo_table (repo_name, path) VALUES (?, ?)",
            (repository_path.name, str(repository_path)),
        )
        repo_id = int(cursor.lastrowid)
        symbols_by_file: dict[str, dict[str, ParsedSymbol]] = {}
        ids_by_file: dict[str, dict[str, int]] = {}
        symbols_by_name: dict[str, list[tuple[str, ParsedSymbol, int]]] = {}
        imports_by_file: dict[str, dict[str, ParsedImport]] = {}
        pending_references: list[tuple[str, ParsedReference]] = []
        repository_modules = {
            str(item.path.relative_to(repository_path))
            .removesuffix(".py")
            .replace("\\", "/")
            .removesuffix("/__init__")
            for item in parsed_files
        }

        for parsed_file in parsed_files:
            relative_path = str(parsed_file.path.relative_to(repository_path))
            file_cursor = connection.execute(
                "INSERT INTO file_table (repo_id, file_name, file_path) VALUES (?, ?, ?)",
                (repo_id, relative_path, str(parsed_file.path)),
            )
            file_id = int(file_cursor.lastrowid)
            connection.execute(
                "INSERT INTO raw_text_table (file_id, hash_code, raw_text) VALUES (?, ?, ?)",
                (file_id, parsed_file.digest, parsed_file.source),
            )

            local_symbols = {s.qualified_name: s for s in parsed_file.walker.symbols}
            local_ids: dict[str, int] = {}
            for symbol in parsed_file.walker.symbols:
                parent_id = (
                    local_ids.get(symbol.parent_qualified_name)
                    if symbol.parent_qualified_name
                    else None
                )
                symbol_id = _insert_symbol(connection, file_id, symbol, parent_id)
                local_ids[symbol.qualified_name] = symbol_id
                symbols_by_name.setdefault(symbol.name, []).append(
                    (relative_path, symbol, symbol_id)
                )

            symbols_by_file[relative_path] = local_symbols
            ids_by_file[relative_path] = local_ids
            pending_references.extend(
                (relative_path, reference)
                for reference in parsed_file.walker.references
            )
            imports_by_file[relative_path] = {
                imported.local_name: imported
                for imported in parsed_file.walker.imports
            }
            for imported in parsed_file.walker.imports:
                connection.execute(
                    """
                    INSERT INTO import_table (
                        file_id, module, imported_name, local_name, line_number
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        imported.module,
                        imported.imported_name,
                        imported.local_name,
                        imported.line_number,
                    ),
                )

        edge_counts = {
            "resolved": 0,
            "ambiguous": 0,
            "unresolved": 0,
            "external": 0,
        }

        # Parent links are also graph edges so callers can traverse containment.
        for file_name, local_symbols in symbols_by_file.items():
            for symbol in local_symbols.values():
                if not symbol.parent_qualified_name:
                    continue
                connection.execute(
                    """
                    INSERT INTO edge_table (
                        repo_id, source_symbol_id, target_symbol_id,
                        relationship_type, target_expression, target_name,
                        line_number, resolution_status
                    ) VALUES (?, ?, ?, 'contains', ?, ?, ?, 'resolved')
                    """,
                    (
                        repo_id,
                        ids_by_file[file_name][symbol.parent_qualified_name],
                        ids_by_file[file_name][symbol.qualified_name],
                        symbol.qualified_name,
                        symbol.name,
                        symbol.line_start,
                    ),
                )
                edge_counts["resolved"] += 1

        for file_name, reference in pending_references:
            target_id, status, relationship = _resolve_reference(
                reference,
                file_name,
                symbols_by_file,
                ids_by_file,
                symbols_by_name,
                imports_by_file,
                repository_modules,
            )
            connection.execute(
                """
                INSERT INTO edge_table (
                    repo_id, source_symbol_id, target_symbol_id,
                    relationship_type, target_expression, target_name,
                    line_number, resolution_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo_id,
                    ids_by_file[file_name][reference.source_qualified_name],
                    target_id,
                    relationship,
                    reference.target_expression,
                    reference.target_name,
                    reference.line_number,
                    status,
                ),
            )
            edge_counts[status] += 1

    symbol_count = sum(len(items) for items in symbols_by_file.values())
    result = (
        f"Parsed {repository_path.name}: {len(parsed_files)} Python files, "
        f"{symbol_count} symbols, {edge_counts['resolved']} resolved relationships, "
        f"{edge_counts['ambiguous']} ambiguous, {edge_counts['unresolved']} unresolved, "
        f"and {edge_counts['external']} external."
    )
    if skipped:
        result += f" Skipped {len(skipped)} file(s): " + ", ".join(skipped[:10])
    return result


def refresh_repository_if_changed(
    repository_path: Path, database_path: Path
) -> str | None:
    """Reparse when file hashes changed or the repository uses the legacy schema."""

    repository_path = repository_path.resolve()
    if not repository_path.is_dir():
        return None
    parsed_files, _ = _parseable_files(repository_path)
    current_hashes = {
        str(item.path.relative_to(repository_path)): item.digest for item in parsed_files
    }

    with closing(sqlite3.connect(database_path)) as connection:
        _create_schema(connection)
        repo_row = connection.execute(
            "SELECT id FROM repo_table WHERE path = ?", (str(repository_path),)
        ).fetchone()
        if repo_row is None:
            return None
        repo_id = repo_row[0]
        stored_rows = connection.execute(
            """
            SELECT f.file_name, t.hash_code
            FROM file_table AS f
            JOIN raw_text_table AS t ON t.file_id = f.id
            WHERE f.repo_id = ?
            """,
            (repo_id,),
        ).fetchall()
        symbol_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM symbol_table AS s
            JOIN file_table AS f ON f.id = s.file_id
            WHERE f.repo_id = ?
            """,
            (repo_id,),
        ).fetchone()[0]
    stored_hashes = {file_name: hash_code for file_name, hash_code in stored_rows}

    if current_hashes == stored_hashes and (symbol_count or not current_hashes):
        return None
    return parse_repository(repository_path, database_path, replace_existing=True)

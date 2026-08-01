"""Parse a cloned Python repository into the call-graph SQLite database."""

from __future__ import annotations

import ast
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS repo_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_name TEXT,
            path TEXT
        );
        CREATE TABLE IF NOT EXISTS file_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER,
            file_name TEXT,
            file_path TEXT,
            FOREIGN KEY (repo_id) REFERENCES repo_table(id)
        );
        CREATE TABLE IF NOT EXISTS function_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER,
            function_name TEXT,
            function_type TEXT,
            line_start INTEGER,
            line_end INTEGER,
            FOREIGN KEY (file_id) REFERENCES file_table(id)
        );
        CREATE TABLE IF NOT EXISTS raw_text_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER,
            hash_code TEXT,
            raw_text TEXT,
            FOREIGN KEY (file_id) REFERENCES file_table(id)
        );
        CREATE TABLE IF NOT EXISTS edge_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER,
            source_type TEXT,
            source_id INTEGER,
            target_type TEXT,
            target_id INTEGER,
            relationship_type TEXT,
            FOREIGN KEY (repo_id) REFERENCES repo_table(id)
        );
        """
    )


def _parseable_files(repository_path: Path) -> tuple[
    list[tuple[Path, str, ast.Module, str]], list[str]
]:
    parsed_files: list[tuple[Path, str, ast.Module, str]] = []
    skipped: list[str] = []
    for path in sorted(repository_path.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            parsed_files.append((path, source, ast.parse(source), _sha256(path)))
        except (OSError, UnicodeError, SyntaxError) as exc:
            skipped.append(f"{path.relative_to(repository_path)} ({type(exc).__name__})")
    return parsed_files, skipped


def _delete_repository(connection: sqlite3.Connection, repo_id: int) -> None:
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
            f"DELETE FROM raw_text_table WHERE file_id IN ({placeholders})", file_ids
        )
        connection.execute(
            f"DELETE FROM function_table WHERE file_id IN ({placeholders})", file_ids
        )
    connection.execute("DELETE FROM file_table WHERE repo_id = ?", (repo_id,))
    connection.execute("DELETE FROM repo_table WHERE id = ?", (repo_id,))


def parse_repository(
    repository_path: Path,
    database_path: Path,
    *,
    replace_existing: bool = False,
) -> str:
    """Insert Python files, functions, source, and call edges in one transaction."""

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
        repo_id = cursor.lastrowid
        functions: dict[str, list[int]] = {}
        calls: list[tuple[int, str]] = []
        raw_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(raw_text_table)")
        }

        for path, source, tree, digest in parsed_files:
            relative_path = str(path.relative_to(repository_path))
            file_cursor = connection.execute(
                "INSERT INTO file_table (repo_id, file_name, file_path) VALUES (?, ?, ?)",
                (repo_id, relative_path, str(path)),
            )
            file_id = file_cursor.lastrowid
            if "hash_code" in raw_columns:
                connection.execute(
                    "INSERT INTO raw_text_table (file_id, hash_code, raw_text) VALUES (?, ?, ?)",
                    (file_id, digest, source),
                )
            else:
                connection.execute(
                    "INSERT INTO raw_text_table "
                    "(file_id, file_hash, function_name, raw_text) VALUES (?, ?, ?, ?)",
                    (file_id, digest, None, source),
                )

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                function_cursor = connection.execute(
                    "INSERT INTO function_table "
                    "(file_id, function_name, function_type, line_start, line_end) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        file_id,
                        node.name,
                        "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                        node.lineno,
                        node.end_lineno,
                    ),
                )
                function_id = function_cursor.lastrowid
                functions.setdefault(node.name, []).append(function_id)
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name):
                            calls.append((function_id, child.func.id))
                        elif isinstance(child.func, ast.Attribute):
                            calls.append((function_id, child.func.attr))

        edge_count = 0
        for source_id, target_name in calls:
            targets = functions.get(target_name, [])
            if len(targets) == 1:
                connection.execute(
                    "INSERT INTO edge_table "
                    "(repo_id, source_type, source_id, target_type, target_id, relationship_type) "
                    "VALUES (?, 'function', ?, 'function', ?, 'calls')",
                    (repo_id, source_id, targets[0]),
                )
                edge_count += 1

    result = (
        f"Parsed {repository_path.name} into the database: "
        f"{len(parsed_files)} Python files, "
        f"{sum(len(ids) for ids in functions.values())} functions, {edge_count} call edges."
    )
    if skipped:
        result += f" Skipped {len(skipped)} file(s): " + ", ".join(skipped[:10])
    return result


def refresh_repository_if_changed(
    repository_path: Path, database_path: Path
) -> str | None:
    """Reparse a repository when its parseable Python-file hash snapshot changed."""

    repository_path = repository_path.resolve()
    if not repository_path.is_dir():
        return None
    parsed_files, _ = _parseable_files(repository_path)
    current_hashes = {
        str(path.relative_to(repository_path)): digest
        for path, _source, _tree, digest in parsed_files
    }

    with closing(sqlite3.connect(database_path)) as connection:
        stored_rows = connection.execute(
            """
            SELECT f.file_name, t.hash_code
            FROM repo_table AS r
            JOIN file_table AS f ON f.repo_id = r.id
            JOIN raw_text_table AS t ON t.file_id = f.id
            WHERE r.path = ?
            """,
            (str(repository_path),),
        ).fetchall()
    stored_hashes = {file_name: hash_code for file_name, hash_code in stored_rows}

    if current_hashes == stored_hashes:
        return None
    return parse_repository(
        repository_path, database_path, replace_existing=True
    )

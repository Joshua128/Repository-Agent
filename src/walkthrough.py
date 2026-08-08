"""Manual parser entry point for local development.

This module deliberately uses the same repository parser as the application so
debug runs and production cannot develop different AST/graph behavior.
"""

from pathlib import Path

from src.repositoryParser import parse_repository


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    repository = project_root / "firstrepo"
    database = project_root / "call_graph.db"
    print(parse_repository(repository, database, replace_existing=True))

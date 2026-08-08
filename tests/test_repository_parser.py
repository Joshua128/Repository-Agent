import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.repositoryParser import parse_repository, refresh_repository_if_changed


class RepositoryParserTests(unittest.TestCase):
    def test_builds_symbol_graph_and_preserves_resolution_status(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            (repository / "base.py").write_text(
                "class Base:\n    def ping(self):\n        return True\n",
                encoding="utf-8",
            )
            (repository / "service.py").write_text(
                '''
import os
from base import Base

class Service(Base):
    def run(self):
        self.helper()
        os.path.join("a", "b")
        missing()

    def helper(self):
        print("done")

def build():
    return Service()
''',
                encoding="utf-8",
            )
            database = root / "graph.db"

            summary = parse_repository(repository, database)
            self.assertIn("symbols", summary)

            with closing(sqlite3.connect(database)) as connection:
                edges = connection.execute(
                    '''
                    SELECT source.qualified_name, e.relationship_type,
                           COALESCE(target.qualified_name, e.target_expression),
                           e.resolution_status
                    FROM edge_table AS e
                    JOIN symbol_table AS source ON source.id = e.source_symbol_id
                    LEFT JOIN symbol_table AS target ON target.id = e.target_symbol_id
                    '''
                ).fetchall()

            self.assertIn(
                ("Service", "inherits", "Base", "resolved"), edges
            )
            self.assertIn(
                ("Service.run", "calls", "Service.helper", "resolved"), edges
            )
            self.assertIn(
                ("build", "instantiates", "Service", "resolved"), edges
            )
            self.assertIn(
                ("Service.run", "calls", "os.path.join", "external"), edges
            )
            self.assertIn(
                ("Service.run", "calls", "missing", "unresolved"), edges
            )
            self.assertIsNone(refresh_repository_if_changed(repository, database))


if __name__ == "__main__":
    unittest.main()

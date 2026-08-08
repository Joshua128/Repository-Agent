import ast
import unittest

from src.NodeWalker import NodeWalk


class NodeWalkTests(unittest.TestCase):
    def test_extracts_scoped_symbols_calls_inheritance_and_imports(self):
        source = '''
from framework import Base as Parent

class Service(Parent):
    """A service."""

    def run(self, value: str) -> bool:
        self.save(value)

        def normalize():
            clean(value)

        return True

    async def save(self, value):
        await persist(value)
'''
        walker = NodeWalk("service.py", "digest", source)
        walker.visit(ast.parse(source))

        symbols = {
            symbol.qualified_name: symbol for symbol in walker.symbols
        }
        self.assertEqual(symbols["Service"].symbol_type, "class")
        self.assertEqual(symbols["Service.run"].symbol_type, "method")
        self.assertEqual(
            symbols["Service.run.normalize"].symbol_type, "function"
        )
        self.assertEqual(symbols["Service.save"].symbol_type, "async_method")
        self.assertEqual(symbols["Service.run"].parent_qualified_name, "Service")
        self.assertEqual(symbols["Service"].docstring, "A service.")

        references = {
            (item.source_qualified_name, item.target_expression): item
            for item in walker.references
        }
        self.assertEqual(
            references[("Service", "Parent")].relationship_type, "inherits"
        )
        self.assertEqual(
            references[("Service.run", "self.save")].target_name, "save"
        )
        self.assertIn(("Service.run.normalize", "clean"), references)
        self.assertEqual(walker.imports[0].local_name, "Parent")


if __name__ == "__main__":
    unittest.main()

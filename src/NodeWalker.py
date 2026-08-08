"""Extract symbols and references from a Python abstract syntax tree."""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass


CALLABLE_TYPES = {"function", "async_function", "method", "async_method"}


@dataclass(frozen=True)
class ParsedSymbol:
    """A class, function, or method defined in one Python source file."""

    name: str
    qualified_name: str
    symbol_type: str
    parent_qualified_name: str | None
    line_start: int
    line_end: int
    file_name: str
    signature: str | None = None
    docstring: str | None = None
    decorators: tuple[str, ...] = ()

    @property
    def resolved_name(self) -> str:
        """Compatibility alias for the name used by the first draft."""

        return self.qualified_name


@dataclass(frozen=True)
class ParsedReference:
    """A syntactic relationship whose target can be resolved later."""

    source_qualified_name: str
    target_expression: str
    target_name: str
    relationship_type: str
    line_number: int

    @property
    def referenced_name(self) -> str:
        """Compatibility alias for the name used by the first draft."""

        return self.source_qualified_name

    @property
    def relation_type(self) -> str:
        return self.relationship_type


# Keep imports separate because aliases are important during reference resolution.
@dataclass(frozen=True)
class ParsedImport:
    module: str | None
    imported_name: str | None
    local_name: str
    line_number: int


# Backwards-compatible name for code that imported the draft dataclass.
ReferencedSymbol = ParsedReference


class NodeWalk(ast.NodeVisitor):
    """Describe definitions and references found in a single Python file.

    This visitor intentionally does not resolve references or write to a database.
    Resolution requires repository-wide knowledge and belongs in a later stage.
    """

    def __init__(self, file_name: str, file_hash: str, source_code: str):
        self.file_name = file_name
        self.file_hash = file_hash
        self.source_code = source_code

        self.symbols: list[ParsedSymbol] = []
        self.references: list[ParsedReference] = []
        self.imports: list[ParsedImport] = []
        self.scope_stack: list[ParsedSymbol] = []

        # Compatibility views used by the original walkthrough/data pipeline.
        # Qualified names prevent methods such as A.save and B.save from colliding.
        self.func_lines: dict[str, tuple[int, int]] = {}
        self.func_stack: list[str] = []
        self.func_dic: defaultdict[str, list[str]] = defaultdict(list)

    def _qualified_name(self, name: str) -> str:
        if not self.scope_stack:
            return name
        return f"{self.scope_stack[-1].qualified_name}.{name}"

    def _current_scope_name(self) -> str | None:
        if not self.scope_stack:
            return None
        return self.scope_stack[-1].qualified_name

    def _current_callable(self) -> ParsedSymbol | None:
        for symbol in reversed(self.scope_stack):
            if symbol.symbol_type in CALLABLE_TYPES:
                return symbol
        return None

    @staticmethod
    def _target_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ast.unparse(node)

    @staticmethod
    def _decorators(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
        return tuple(ast.unparse(decorator) for decorator in node.decorator_list)

    @staticmethod
    def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"

    @staticmethod
    def _class_signature(node: ast.ClassDef) -> str:
        arguments = [ast.unparse(base) for base in node.bases]
        arguments.extend(
            f"{keyword.arg}={ast.unparse(keyword.value)}" for keyword in node.keywords
        )
        suffix = f"({', '.join(arguments)})" if arguments else ""
        return f"class {node.name}{suffix}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = self._qualified_name(node.name)
        symbol = ParsedSymbol(
            name=node.name,
            qualified_name=qualified_name,
            symbol_type="class",
            parent_qualified_name=self._current_scope_name(),
            line_start=node.lineno,
            line_end=node.end_lineno,
            file_name=self.file_name,
            signature=self._class_signature(node),
            docstring=ast.get_docstring(node),
            decorators=self._decorators(node),
        )
        self.symbols.append(symbol)

        for base in node.bases:
            expression = ast.unparse(base)
            self.references.append(
                ParsedReference(
                    source_qualified_name=qualified_name,
                    target_expression=expression,
                    target_name=self._target_name(base),
                    relationship_type="inherits",
                    line_number=node.lineno,
                )
            )

        self.scope_stack.append(symbol)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent = self.scope_stack[-1] if self.scope_stack else None
        is_method = parent is not None and parent.symbol_type == "class"
        is_async = isinstance(node, ast.AsyncFunctionDef)
        if is_method:
            symbol_type = "async_method" if is_async else "method"
        else:
            symbol_type = "async_function" if is_async else "function"

        qualified_name = self._qualified_name(node.name)
        symbol = ParsedSymbol(
            name=node.name,
            qualified_name=qualified_name,
            symbol_type=symbol_type,
            parent_qualified_name=self._current_scope_name(),
            line_start=node.lineno,
            line_end=node.end_lineno,
            file_name=self.file_name,
            signature=self._function_signature(node),
            docstring=ast.get_docstring(node),
            decorators=self._decorators(node),
        )
        self.symbols.append(symbol)
        self.func_lines[qualified_name] = (node.lineno, node.end_lineno)

        self.scope_stack.append(symbol)
        self.func_stack.append(qualified_name)
        self.generic_visit(node)
        self.func_stack.pop()
        self.scope_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        source = self._current_callable()
        if source is not None:
            expression = ast.unparse(node.func)
            target_name = self._target_name(node.func)
            self.references.append(
                ParsedReference(
                    source_qualified_name=source.qualified_name,
                    target_expression=expression,
                    target_name=target_name,
                    relationship_type="calls",
                    line_number=node.lineno,
                )
            )
            self.func_dic[source.qualified_name].append(target_name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(
                ParsedImport(
                    module=alias.name,
                    imported_name=None,
                    local_name=alias.asname or alias.name.split(".")[0],
                    line_number=node.lineno,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level
        module = f"{prefix}{node.module or ''}"
        for alias in node.names:
            self.imports.append(
                ParsedImport(
                    module=module,
                    imported_name=alias.name,
                    local_name=alias.asname or alias.name,
                    line_number=node.lineno,
                )
            )

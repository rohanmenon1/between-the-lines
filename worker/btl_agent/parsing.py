"""Discover declarations and compare parsed code, never source formatting."""

import ast
from functools import lru_cache
import json
import re

from .types import Declaration, WorkerError, digest, source_lines


PYTHON_DECLARATIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
JAVA_DECLARATIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
    "method_declaration": "method",
    "constructor_declaration": "constructor",
    "compact_constructor_declaration": "constructor",
}


def parse_python(source: str):
    # Byte parsing honors an encoding cookie just as Python execution does.
    tree = ast.parse(source.encode("utf-8"))
    if ast.dump(tree) != ast.dump(ast.parse(source.removeprefix("\ufeff"))):
        raise WorkerError("unsafe_encoding", "Use UTF-8 source encoding for annotation.")
    return tree


@lru_cache(maxsize=1)
def java_language():
    try:
        from tree_sitter import Language
        import tree_sitter_java
    except ImportError as exc:
        raise WorkerError("runtime_unavailable", "The Java parser is unavailable.") from exc
    return Language(tree_sitter_java.language())


def parse_java(source: str):
    try:
        from tree_sitter import Parser
    except ImportError as exc:
        raise WorkerError("runtime_unavailable", "The Java parser is unavailable.") from exc

    # Java expands these before lexing; Tree-sitter does not model that step.
    if re.search(r"\\u+[0-9a-fA-F]{4}", source):
        raise WorkerError("unsafe_source", "Java Unicode escapes are not supported safely.")
    if re.search(r"\r(?!\n)", source):
        raise WorkerError("unsafe_source", "Use LF or CRLF line endings for Java annotation.")
    tree = Parser(java_language()).parse(source.encode("utf-8"))
    if tree.root_node.has_error:
        raise SyntaxError("Java source contains invalid or incomplete syntax.")
    return tree


def java_fingerprint(node, data: bytes):
    if node.type in {"line_comment", "block_comment"}:
        return None
    children = tuple(
        value for child in node.children
        if (value := java_fingerprint(child, data)) is not None
    )
    return (node.type, children) if node.children else (
        node.type, data[node.start_byte:node.end_byte].decode("utf-8")
    )


def fingerprint(source: str, language: str) -> str:
    try:
        if language == "python":
            return ast.dump(parse_python(source), include_attributes=False)
        if language == "java":
            return json.dumps(java_fingerprint(parse_java(source).root_node, source.encode("utf-8")))
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise WorkerError("invalid_source", "Source must parse successfully before annotation.") from exc
    raise WorkerError("invalid_request", "Supported languages are python and java.")


def discover(source: str, language: str) -> list[Declaration]:
    # Validate first so discovery and whole-source validation share semantics.
    fingerprint(source, language)
    lines = source_lines(source)
    declarations = []
    seen = {}

    def symbol_id(qualified: str) -> str:
        seen[qualified] = seen.get(qualified, 0) + 1
        suffix = f"#{seen[qualified]}" if seen[qualified] > 1 else ""
        return f"{language}:{qualified}{suffix}"

    if language == "python":
        def visit_python(node, parents):
            scope = parents
            if isinstance(node, PYTHON_DECLARATIONS):
                scope = (*parents, node.name)
                row = min((x.lineno for x in node.decorator_list), default=node.lineno) - 1
                text = "".join(lines[row:node.end_lineno])
                declarations.append(Declaration(
                    symbol_id(".".join(scope)), node.name,
                    "class" if isinstance(node, ast.ClassDef) else "function",
                    row, node.end_lineno - 1, text,
                    digest(ast.dump(node, include_attributes=False)),
                    ast.get_docstring(node) is not None,
                ))
            for child in ast.iter_child_nodes(node):
                visit_python(child, scope)
        visit_python(parse_python(source), ())
    else:
        data = source.encode("utf-8")

        def text_of(node):
            return data[node.start_byte:node.end_byte].decode("utf-8")

        def visit_java(node, parents):
            scope = parents
            kind = JAVA_DECLARATIONS.get(node.type)
            if kind:
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = text_of(name_node)
                    # Parameters identify overloads independently of positions/body.
                    parameters = node.child_by_field_name("parameters")
                    signature = name
                    if parameters:
                        signature += "(" + digest(json.dumps(java_fingerprint(parameters, data)))[:12] + ")"
                    scope = (*parents, signature)
                    row = node.start_point.row
                    prefix = lines[row].encode("utf-8")[:node.start_point.column]
                    if not prefix.strip():
                        declarations.append(Declaration(
                            symbol_id(".".join(scope)), name, kind, row,
                            node.end_point.row, text_of(node),
                            digest(json.dumps(java_fingerprint(node, data))),
                        ))
            for child in node.children:
                visit_java(child, scope)
        visit_java(parse_java(source).root_node, ())
    return sorted(declarations, key=lambda item: (item.start_line, item.end_line))


def changed_declarations(current, previous, force=False):
    old_hashes = {item.symbol_id: item.code_hash for item in previous}
    changed = [item for item in current if force or old_hashes.get(item.symbol_id) != item.code_hash]
    # A changed method/function is more useful than an overlapping class comment.
    leaves = []
    for index, item in enumerate(changed):
        next_item = changed[index + 1] if index + 1 < len(changed) else None
        # Source declarations form nested/disjoint intervals in source order.
        # The next changed declaration is sufficient to detect a child.
        if next_item and item.start_line <= next_item.start_line and next_item.end_line <= item.end_line:
            continue
        leaves.append(item)
    return leaves

"""Comment-only Java annotation with syntax-tree validation."""

from functools import lru_cache

from .annotate import AnnotationResult, BlockInfo, generate_summary
from .model import ModelUnavailableError, generate_comment


DECLARATIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
    "method_declaration": "method",
    "constructor_declaration": "constructor",
}


@lru_cache(maxsize=1)
def _language():
    from tree_sitter import Language
    import tree_sitter_java

    return Language(tree_sitter_java.language())


def _parser():
    """Create a parser per request; Parser instances are mutable."""
    from tree_sitter import Parser

    return Parser(_language())


def parse_java(source: str):
    tree = _parser().parse(source.encode("utf-8"))
    if tree.root_node.has_error:
        raise SyntaxError("Java parser found an error or missing syntax")
    return tree


def _fingerprint(node, source_bytes: bytes):
    """Keep node types and exact leaf tokens; omit comments and positions."""
    if node.type in {"line_comment", "block_comment"}:
        return None
    children = tuple(
        child_fingerprint
        for child in node.children
        if (child_fingerprint := _fingerprint(child, source_bytes)) is not None
    )
    if node.children:
        return node.type, children
    return node.type, source_bytes[node.start_byte : node.end_byte]


def java_syntax_fingerprint(source: str):
    tree = parse_java(source)
    return _fingerprint(tree.root_node, source.encode("utf-8"))


def collect_java_blocks(tree, source: str) -> list[BlockInfo]:
    """Find declarations that can receive a comment without moving code."""
    lines = source.splitlines()
    source_bytes = source.encode("utf-8")
    blocks: list[BlockInfo] = []

    def visit(node):
        kind = DECLARATIONS.get(node.type)
        if kind:
            row = node.start_point.row
            # Inserting above a declaration that shares a line with code could
            # comment out that code, so those declarations are skipped.
            if row < len(lines):
                line_prefix = lines[row].encode("utf-8")[: node.start_point.column]
                if not line_prefix.strip():
                    name_node = node.child_by_field_name("name")
                    if name_node is not None:
                        name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8")
                        block_source = source_bytes[node.start_byte : node.end_byte].decode("utf-8")
                        blocks.append(BlockInfo(kind, name, row + 1, block_source))
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return sorted(blocks, key=lambda block: block.lineno)


def _insert_comments(source: str, comments: dict[int, str]) -> str:
    lines = source.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in source else "\n"
    output: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        comment = comments.get(line_number)
        if comment is not None:
            indent = line[: len(line) - len(line.lstrip())]
            output.append(indent + comment + newline)
        output.append(line)
    return "".join(output)


def annotate_java(source: str, model_choice: str = "base") -> AnnotationResult:
    if not source.strip():
        return AnnotationResult("", "", "Paste a Java file to annotate.", False, 0)
    if model_choice != "base":
        return AnnotationResult("", source, "Java currently supports only the base Mellum2 model; the LoRA adapter was trained on Python.", False, 0)

    try:
        tree = parse_java(source)
    except SyntaxError as exc:
        return AnnotationResult("", source, f"Java syntax error: {exc}", False, 0)

    blocks = collect_java_blocks(tree, source)
    summary, summary_notes = generate_summary(source, blocks, "base", language="java")
    if not blocks:
        status = "Parsed successfully. No standalone Java declarations to annotate."
        if summary_notes:
            status += "\n" + "\n".join(summary_notes)
        return AnnotationResult(summary, source, status, True, 0, tuple(summary_notes))

    comments: dict[int, str] = {}
    notes = list(summary_notes)
    for block in blocks:
        try:
            comment = generate_comment(block.kind, block.name, block.source, variant="base", language="java")
        except ModelUnavailableError:
            raise
        except Exception as exc:
            comment = f"// TODO(model): Could not explain {block.kind} {block.name}."
            notes.append(f"{block.name}: model generation failed ({type(exc).__name__}).")
        # Java processes Unicode escapes before tokenization, including inside
        # comments. An escape in generated text could change the code below it.
        if not comment.startswith("// ") or "\n" in comment or "\r" in comment or "\\u" in comment:
            return AnnotationResult(summary, source, "Rejected: generated Java comment contains unsafe syntax.", False, len(blocks), tuple(notes))
        comments[block.lineno] = comment

    annotated = _insert_comments(source, comments)
    try:
        same_tree = java_syntax_fingerprint(source) == java_syntax_fingerprint(annotated)
    except SyntaxError:
        same_tree = False
    status = (
        f"Validated: parsed {len(blocks)} Java declaration(s), inserted comments, non-comment syntax tree unchanged."
        if same_tree
        else "Rejected: annotation changed or broke the Java syntax tree."
    )
    if notes:
        status += "\n" + "\n".join(notes)
    return AnnotationResult(summary, annotated if same_tree else source, status, same_tree, len(blocks), tuple(notes))

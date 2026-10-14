import ast

from .core import (
    AnnotationResult,
    BlockInfo,
    generate_summary,
    insert_comments,
)
from .model import ModelUnavailableError, ModelVariant, generate_comment


def parse_python(source: str) -> ast.Module:
    return ast.parse(source)


def semantic_ast_dump(source: str) -> str:
    """Honor Python encoding declarations; ignore positions but retain docstrings."""
    return ast.dump(ast.parse(source.encode("utf-8")), include_attributes=False)


def _has_preceding_python_comment(lines: list[str], lineno: int) -> bool:
    """An adjacent comment belongs to the declaration, except file directives."""
    if lineno <= 1:
        return False
    preceding = lines[lineno - 2].strip()
    return preceding.startswith("#") and not preceding.startswith(
        ("#!", "# -*- coding", "# coding:")
    )


def collect_blocks(tree: ast.Module, source: str) -> list[BlockInfo]:
    blocks: list[BlockInfo] = []
    lines = source.splitlines(keepends=True)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        kind = (
            "class"
            if isinstance(node, ast.ClassDef)
            else "async function"
            if isinstance(node, ast.AsyncFunctionDef)
            else "function"
        )
        # ast gives decorated definitions the line of `def` or `class`.
        # Insert before the entire decorator group to keep it attached.
        lineno = min(
            (decorator.lineno for decorator in node.decorator_list), default=node.lineno
        )
        if node.decorator_list:
            block_source = "".join(lines[lineno - 1 : node.end_lineno]).rstrip("\r\n")
        else:
            block_source = ast.get_source_segment(source, node) or ast.unparse(node)
        documented = bool(ast.get_docstring(node)) or _has_preceding_python_comment(
            lines, lineno
        )
        blocks.append(BlockInfo(kind, node.name, lineno, block_source, documented))
    return sorted(blocks, key=lambda item: item.lineno)


def generate_block_comments(
    blocks: list[BlockInfo], model_choice: ModelVariant = "base"
) -> tuple[dict[int, str], list[str]]:
    comments: dict[int, str] = {}
    notes: list[str] = []

    for block in blocks:
        if block.documented:
            continue
        try:
            comment = generate_comment(
                block.kind, block.name, block.source, variant=model_choice
            )
            if not comment.strip("# "):
                notes.append(f"{block.name}: model returned an empty comment.")
                continue
            comments[block.lineno] = comment
        except ModelUnavailableError:
            raise
        except Exception as exc:
            notes.append(
                f"{block.name}: model generation failed ({type(exc).__name__})."
            )
    return comments, notes


def annotate_python(
    source: str, model_choice: ModelVariant = "base"
) -> AnnotationResult:
    source = source.removeprefix("\ufeff")
    if not source.strip():
        return AnnotationResult("", "", "Paste a Python file to annotate.", False, 0)

    try:
        original_tree = parse_python(source)
        original_ast = semantic_ast_dump(source)
    except SyntaxError as exc:
        return AnnotationResult(
            "", "", f"Syntax error on line {exc.lineno}: {exc.msg}", False, 0
        )

    blocks = collect_blocks(original_tree, source)
    summary, summary_notes = generate_summary(source, blocks, model_choice)
    if not blocks:
        status = "Parsed successfully. No classes or functions to annotate."
        if summary_notes:
            status += "\n" + "\n".join(summary_notes)
        return AnnotationResult(summary, source, status, True, 0, tuple(summary_notes))

    comments, generation_notes = generate_block_comments(blocks, model_choice)
    generation_notes = summary_notes + generation_notes
    annotated = insert_comments(source, comments)

    try:
        same_ast = original_ast == semantic_ast_dump(annotated)
    except SyntaxError as exc:
        return AnnotationResult(
            "",
            source,
            f"Generated annotation failed to parse on line {exc.lineno}: {exc.msg}",
            False,
            len(blocks),
            tuple(generation_notes),
        )

    status = (
        f"Validated: parsed {len(blocks)} block(s), inserted {len(comments)} comment(s), AST unchanged."
        if same_ast
        else "Rejected: annotation changed the semantic AST."
    )
    if generation_notes:
        status += "\n" + "\n".join(generation_notes)

    return AnnotationResult(
        summary,
        annotated if same_ast else source,
        status,
        same_ast,
        len(blocks),
        tuple(generation_notes),
    )


def annotate_source(
    source: str, model_choice: ModelVariant = "base", language: str = "python"
) -> AnnotationResult:
    if language == "python":
        return annotate_python(source, model_choice)
    if language == "java":
        from .java import annotate_java

        return annotate_java(source, model_choice)
    return AnnotationResult("", source, f"Unsupported language: {language}", False, 0)

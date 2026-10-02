import ast
from dataclasses import dataclass
from typing import Literal

from .model import ModelUnavailableError, generate_comment, generate_file_summary


ModelChoice = Literal["base", "tuned"]


@dataclass(frozen=True)
class BlockInfo:
    kind: str
    name: str
    lineno: int
    source: str


@dataclass(frozen=True)
class AnnotationResult:
    summary: str
    annotated_source: str
    status: str
    valid: bool
    block_count: int
    notes: tuple[str, ...] = ()


def parse_python(source: str) -> ast.Module:
    return ast.parse(source)


def semantic_ast_dump(source: str) -> str:
    """Compare code and docstrings while ignoring source positions."""
    return ast.dump(parse_python(source), include_attributes=False)


def collect_blocks(tree: ast.Module, source: str) -> list[BlockInfo]:
    blocks: list[BlockInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            block_source = ast.get_source_segment(source, node) or ast.unparse(node)
            blocks.append(BlockInfo("class", node.name, node.lineno, block_source))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            block_source = ast.get_source_segment(source, node) or ast.unparse(node)
            blocks.append(BlockInfo(kind, node.name, node.lineno, block_source))
    return sorted(blocks, key=lambda item: item.lineno)


def summarize_blocks(blocks: list[BlockInfo]) -> str:
    if not blocks:
        return "This Python file has no classes or functions to annotate yet."
    names = ", ".join(f"{block.kind} `{block.name}`" for block in blocks[:8])
    overflow = "" if len(blocks) <= 8 else f", plus {len(blocks) - 8} more"
    return f"This file defines {names}{overflow}."


def generate_summary(source: str, blocks: list[BlockInfo], model_choice: ModelChoice = "base", language: str = "python") -> tuple[str, list[str]]:
    notes: list[str] = []
    try:
        summary = generate_file_summary(source, variant=model_choice, language=language)
        if summary:
            return summary, notes
        notes.append("file summary: model returned an empty summary.")
    except ModelUnavailableError:
        raise
    except Exception as exc:
        notes.append(f"file summary: model generation failed ({type(exc).__name__}).")
    return summarize_blocks(blocks), notes


def insert_comments(source: str, comments: dict[int, str]) -> str:
    lines = source.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in source else "\n"
    inserts: dict[int, list[str]] = {}
    for lineno, comment_text in comments.items():
        line = lines[lineno - 1]
        indent = line[: len(line) - len(line.lstrip())]
        inserts.setdefault(lineno - 1, []).append(indent + comment_text + newline)

    annotated: list[str] = []
    for index, line in enumerate(lines):
        annotated.extend(inserts.get(index, []))
        annotated.append(line)
    return "".join(annotated)


def generate_block_comments(blocks: list[BlockInfo], model_choice: ModelChoice = "base") -> tuple[dict[int, str], list[str]]:
    comments: dict[int, str] = {}
    notes: list[str] = []

    for block in blocks:
        try:
            comments[block.lineno] = generate_comment(block.kind, block.name, block.source, variant=model_choice)
        except ModelUnavailableError:
            raise
        except Exception as exc:
            comments[block.lineno] = f"# TODO(model): Could not explain {block.kind} `{block.name}`."
            notes.append(f"{block.name}: model generation failed ({type(exc).__name__}).")
    return comments, notes


def annotate_python(source: str, model_choice: ModelChoice = "base") -> AnnotationResult:
    source = source.strip("\ufeff")
    if not source.strip():
        return AnnotationResult("", "", "Paste a Python file to annotate.", False, 0)

    try:
        original_tree = parse_python(source)
    except SyntaxError as exc:
        return AnnotationResult("", "", f"Syntax error on line {exc.lineno}: {exc.msg}", False, 0)

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
        same_ast = semantic_ast_dump(source) == semantic_ast_dump(annotated)
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
        f"Validated: parsed {len(blocks)} block(s), inserted comments, AST unchanged."
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


def annotate_source(source: str, model_choice: ModelChoice = "base", language: str = "python") -> AnnotationResult:
    if language == "python":
        return annotate_python(source, model_choice)
    if language == "java":
        from .java import annotate_java

        return annotate_java(source, model_choice)
    return AnnotationResult("", source, f"Unsupported language: {language}", False, 0)

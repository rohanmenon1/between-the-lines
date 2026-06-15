import ast
from dataclasses import dataclass
from typing import Literal

from .model import ModelUnavailableError, generate_comment, generate_comment_with_llm, load_llm


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


def strip_docstrings(node: ast.AST) -> ast.AST:
    copied = ast.fix_missing_locations(ast.parse(ast.unparse(node)))
    for child in ast.walk(copied):
        if isinstance(child, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                child.body
                and isinstance(child.body[0], ast.Expr)
                and isinstance(child.body[0].value, ast.Constant)
                and isinstance(child.body[0].value.value, str)
            ):
                child.body = child.body[1:]
    return copied


def semantic_ast_dump(source: str) -> str:
    tree = parse_python(source)
    stripped = strip_docstrings(tree)
    return ast.dump(stripped, include_attributes=False)


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


def insert_comments(source: str, comments: dict[int, str]) -> str:
    lines = source.splitlines()
    inserts: dict[int, list[str]] = {}
    for lineno, comment_text in comments.items():
        indent = len(lines[lineno - 1]) - len(lines[lineno - 1].lstrip())
        inserts.setdefault(lineno - 1, []).append(" " * indent + comment_text)

    annotated: list[str] = []
    for index, line in enumerate(lines):
        annotated.extend(inserts.get(index, []))
        annotated.append(line)
    return "\n".join(annotated) + ("\n" if source.endswith("\n") else "")


def generate_block_comments(blocks: list[BlockInfo], model_choice: ModelChoice = "base") -> tuple[dict[int, str], list[str]]:
    comments: dict[int, str] = {}
    notes: list[str] = []
    llm = load_llm() if model_choice == "base" else None

    for block in blocks:
        try:
            if model_choice == "base":
                comments[block.lineno] = generate_comment_with_llm(llm, block.kind, block.name, block.source)
            else:
                comments[block.lineno] = generate_comment(block.kind, block.name, block.source, variant="tuned")
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
    summary = summarize_blocks(blocks)
    if not blocks:
        return AnnotationResult(summary, source, "Parsed successfully. No classes or functions to annotate.", True, 0)

    comments, generation_notes = generate_block_comments(blocks, model_choice)
    annotated = insert_comments(source, comments)

    try:
        same_ast = semantic_ast_dump(source) == semantic_ast_dump(annotated)
    except SyntaxError as exc:
        return AnnotationResult(
            "",
            annotated,
            f"Generated annotation failed to parse on line {exc.lineno}: {exc.msg}",
            False,
            len(blocks),
            tuple(generation_notes),
        )

    status = (
        f"Validated: parsed {len(blocks)} block(s), inserted {model_choice} model comments, semantic AST unchanged."
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

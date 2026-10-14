"""Shared results, comment insertion, and summaries for both languages."""

from dataclasses import dataclass

from .model import ModelUnavailableError, ModelVariant, generate_file_summary


@dataclass(frozen=True)
class BlockInfo:
    kind: str
    name: str
    lineno: int
    source: str
    documented: bool = False


@dataclass(frozen=True)
class AnnotationResult:
    summary: str
    annotated_source: str
    status: str
    valid: bool
    block_count: int
    notes: tuple[str, ...] = ()


def insert_comments(source: str, comments: dict[int, str]) -> str:
    """Insert a comment before its declaration, retaining every original line."""
    newline = "\r\n" if "\r\n" in source else "\n"
    output: list[str] = []
    for line_number, line in enumerate(source.splitlines(keepends=True), start=1):
        comment = comments.get(line_number)
        if comment is not None:
            indent = line[: len(line) - len(line.lstrip())]
            output.append(indent + comment + newline)
        output.append(line)
    return "".join(output)


def summarize_blocks(blocks: list[BlockInfo], language: str = "python") -> str:
    if not blocks:
        return f"This {language.title()} file has no declarations to annotate."
    names = ", ".join(f"{block.kind} `{block.name}`" for block in blocks[:8])
    overflow = "" if len(blocks) <= 8 else f", plus {len(blocks) - 8} more"
    return f"This file defines {names}{overflow}."


def generate_summary(
    source: str,
    blocks: list[BlockInfo],
    model_choice: ModelVariant = "base",
    language: str = "python",
) -> tuple[str, list[str]]:
    if model_choice == "local":
        return summarize_blocks(blocks, language), []
    try:
        summary = generate_file_summary(source, variant=model_choice, language=language)
        if summary:
            return summary, []
        note = "file summary: model returned an empty summary."
    except ModelUnavailableError:
        raise
    except Exception as exc:
        note = f"file summary: model generation failed ({type(exc).__name__})."
    return summarize_blocks(blocks, language), [note]

"""Construct one comment edit and validate it against the complete source."""

import re
import unicodedata

from .parsing import fingerprint
from .types import WorkerError, digest, source_lines


DIRECTIVE = re.compile(r"^#.*(?:coding\s*[:=]|noqa\b|type\s*:|fmt\s*:|pylint\s*:|ruff\s*:)")
ENCODING_COOKIE = re.compile(r"^#.*coding\s*[:=]\s*[-\w.]+")


def preceding_comment(source, declaration, owned):
    """Return an exact owned line, or preserve adjacent human documentation."""
    if declaration.has_docstring:
        return "human", None
    lines = source_lines(source)
    row = declaration.start_line
    if row == 0:
        return "none", None
    def visible(index):
        value = lines[index].removeprefix("\ufeff") if index == 0 else lines[index]
        return value.strip()

    text = visible(row - 1)
    is_comment = text.startswith(("#", "//")) or text.endswith("*/")
    if not is_comment or text.startswith("#!") or (row <= 2 and ENCODING_COOKIE.match(text)):
        return "none", None
    match = next((item for item in owned
                  if item["symbolId"] == declaration.symbol_id
                  and item["commentText"] == text), None)
    if match and not DIRECTIVE.match(text):
        # An adjoining human comment block must remain the sole documentation.
        if row >= 2 and visible(row - 2).startswith(("#", "//", "/*", "*")):
            return "human", None
        return "owned", row - 1
    return "human", None


def make_suggestion(source, language, declaration, comment, explanation, evidence, owned):
    prefix = "# " if language == "python" else "// "
    if not isinstance(comment, str):
        raise WorkerError("unsafe_proposal", "Comment must be plain text.")
    comment = comment.strip()
    # Only deterministic line comments, never model-written source or directives.
    if comment.startswith(prefix):
        comment = comment[len(prefix):].strip()
    if (not comment or len(comment) > 240 or "\\u" in comment
            or any(unicodedata.category(char).startswith("C") for char in comment)
            or "\u2028" in comment or "\u2029" in comment):
        raise WorkerError("unsafe_proposal", "Comment contains unsafe or excessive text.")
    comment_text = prefix + comment
    if language == "python" and (DIRECTIVE.match(comment_text) or comment.startswith("!")):
        raise WorkerError("unsafe_proposal", "Generated directives are not allowed.")
    ownership, owned_row = preceding_comment(source, declaration, owned)
    if ownership == "human":
        raise WorkerError("human_comment", "Existing documentation is preserved.")
    lines = source_lines(source)
    row = declaration.start_line
    declaration_line = lines[row]
    indent = declaration_line[:len(declaration_line) - len(declaration_line.lstrip(" \t"))]
    newline = "\r\n" if "\r\n" in source else "\r" if "\r" in source and "\n" not in source else "\n"
    replacement = indent + comment_text + newline
    character = 1 if row == 0 and source.startswith("\ufeff") else 0
    if ownership == "owned":
        row = owned_row
        character = 1 if row == 0 and lines[row].startswith("\ufeff") else 0
        old_text = lines[row][character:]
        if old_text.rstrip("\r\n") == replacement.rstrip("\r\n"):
            return None
        start = sum(map(len, lines[:row])) + character
        end = start + len(old_text)
        end_position = {"line": row + 1, "character": 0}
    else:
        old_text = ""
        start = sum(map(len, lines[:row])) + character
        end = start
        end_position = {"line": row, "character": character}
    patched = source[:start] + replacement + source[end:]
    if fingerprint(source, language) != fingerprint(patched, language):
        raise WorkerError("unsafe_proposal", "The proposal changes parsed code.")
    return {
        "id": digest(declaration.symbol_id + digest(source) + replacement)[:24],
        "symbolId": declaration.symbol_id, "symbolName": declaration.name,
        "range": {"start": {"line": row, "character": character}, "end": end_position},
        "oldText": old_text, "newText": replacement, "commentText": comment_text,
        "codeHash": declaration.code_hash, "explanation": explanation,
        "evidence": evidence,
    }


def apply_suggestions(source, language, suggestions):
    """Validate and apply one complete comment batch against the same snapshot."""
    lines = source_lines(source)
    edits = []
    for suggestion in suggestions:
        offsets = []
        for position in (suggestion["range"]["start"], suggestion["range"]["end"]):
            row, column = position["line"], position["character"]
            if (type(row) is not int or type(column) is not int or row < 0
                    or row > len(lines) or column < 0
                    or (row == len(lines) and column != 0)
                    or (row < len(lines) and column > len(lines[row].rstrip("\r\n")))):
                raise WorkerError("unsafe_proposal", "A comment batch contains an invalid range.")
            offsets.append(sum(map(len, lines[:row])) + column)
        start, end = offsets
        if end < start or source[start:end] != suggestion["oldText"]:
            raise WorkerError("unsafe_proposal", "A comment batch does not match its source snapshot.")
        edits.append((start, end, suggestion["newText"]))
    edits.sort()
    for previous, current in zip(edits, edits[1:]):
        if previous[1] > current[0] or previous[0] == current[0]:
            raise WorkerError("unsafe_proposal", "Comment edits overlap.")
    patched = source
    for start, end, replacement in reversed(edits):
        patched = patched[:start] + replacement + patched[end:]
    if fingerprint(source, language) != fingerprint(patched, language):
        raise WorkerError("unsafe_proposal", "The comment batch changes parsed code.")
    return patched

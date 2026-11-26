"""Validate and apply one reviewed Python comment."""

import ast
import difflib
import os
import re
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class Preview:
    approval_id: str
    source: bytes
    evidence: str
    diff: str


def preview(source: bytes, line: int, comment: str, prompt: str, evidence: str) -> Preview:
    """Insert a single plain comment immediately above a function definition."""
    if not comment.strip() or len(comment) > 240 or not comment.isprintable():
        raise ValueError("Use one printable line of at most 240 characters.")
    if not evidence.strip() or evidence not in prompt:
        raise ValueError("Evidence must be an exact excerpt from the original prompt.")

    text = source.decode("utf-8-sig")
    tree = ast.parse(text, type_comments=True)
    function = next(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == line),
        None,
    )
    if function is None:
        raise ValueError("Select the first line of a Python function definition.")

    anchor = min([line, *(item.lineno for item in function.decorator_list)]) - 1
    # Keep the original bytes, including mixed newline styles.
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text)
    original = lines[anchor]
    indent = original[:len(original) - len(original.lstrip(" \t"))]
    newline = "\r\n" if original.endswith("\r\n") else "\r" if original.endswith("\r") else "\n"
    updated = "".join([*lines[:anchor], indent + "# " + comment.strip() + newline, *lines[anchor:]])
    changed = ast.parse(updated, type_comments=True)
    if ast.dump(tree) != ast.dump(changed):
        raise ValueError("The comment changes the Python AST.")
    # AST equality omits source-position attributes. Type ignores carry their
    # target line in the tree itself and are conservatively rejected if shifted.
    encoding = "utf-8-sig" if source.startswith(b"\xef\xbb\xbf") else "utf-8"
    diff = "".join(difflib.unified_diff(lines, updated.splitlines(keepends=True),
                                      fromfile="before", tofile="after", n=2))
    result = updated.encode(encoding)
    identity = b"".join(sha256(part).digest() for part in
                        (source, result, prompt.encode(), evidence.encode()))
    return Preview(sha256(identity).hexdigest(), result, evidence, diff)


def source_path(workspace: Path, relative: str) -> Path:
    """Resolve a regular Python file within the workspace."""
    root = workspace.resolve(strict=True)
    candidate = root / relative
    if Path(relative).is_absolute() or candidate.is_symlink():
        raise ValueError("Select a regular Python file under the workspace.")
    path = candidate.resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix != ".py" or not path.is_file():
        raise ValueError("Select a regular Python file under the workspace.")
    return path


def apply(workspace: Path, relative: str, approval_id: str, line: int,
          comment: str, prompt: str, evidence: str) -> None:
    """Rebuild a reviewed patch from the saved file and replace it atomically."""
    path = source_path(workspace, relative)
    source = path.read_bytes()
    checked = preview(source, line, comment, prompt, evidence)
    if approval_for(path, checked) != approval_id:
        raise ValueError("The file or proposal changed. Review a new preview.")
    target_mode = stat.S_IMODE(path.stat().st_mode)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        staged = Path(stream.name)
        try:
            stream.write(checked.source)
            stream.close()
            staged.chmod(target_mode)
            if source_path(workspace, relative) != path or path.read_bytes() != source:
                raise ValueError("The file changed. Review a new preview.")
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)


def approval_for(path: Path, checked: Preview) -> str:
    return sha256(bytes.fromhex(checked.approval_id) + os.fsencode(path)).hexdigest()

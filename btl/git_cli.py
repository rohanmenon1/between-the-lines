"""Annotate staged files locally before committing them."""

import argparse
import codecs
import difflib
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys

from .annotate import annotate_source
from .model import DEFAULT_LOCAL_MODEL, ModelUnavailableError


class GitCommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileChange:
    git_path: str
    path: Path
    original: bytes
    updated: bytes


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode not in (0, 1) or (
        result.returncode == 1 and args[0] != "diff"
    ):
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitCommandError(detail or f"git {args[0]} failed")
    return result


def _repo_root() -> Path:
    result = _git("rev-parse", "--show-toplevel")
    return Path(result.stdout.decode("utf-8").strip()).resolve()


def _staged_sources(root: Path) -> list[tuple[str, str]]:
    result = _git(
        "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", cwd=root
    )
    files: list[tuple[str, str]] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        extension = Path(path).suffix.lower()
        if extension in {".py", ".java"}:
            files.append((path, "python" if extension == ".py" else "java"))
    return files


def _read_clean_file(root: Path, git_path: str) -> tuple[Path, bytes, str]:
    path = root / git_path
    if (
        path.is_symlink()
        or not path.is_file()
        or not path.resolve().is_relative_to(root)
    ):
        raise GitCommandError(
            f"{git_path}: expected a regular file inside the repository"
        )
    if _git("ls-files", "-u", "-z", "--", git_path, cwd=root).stdout:
        raise GitCommandError(f"{git_path}: resolve the merge conflict first")
    if _git("diff", "--quiet", "--", git_path, cwd=root).returncode:
        raise GitCommandError(
            f"{git_path}: unstaged changes found; stage the whole file or restore them first"
        )

    original = path.read_bytes()
    try:
        source = original.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GitCommandError(
            f"{git_path}: only UTF-8 source files are supported"
        ) from exc
    return path, original, source


def _proposed_bytes(original: bytes, annotated: str) -> bytes:
    bom = codecs.BOM_UTF8 if original.startswith(codecs.BOM_UTF8) else b""
    return bom + annotated.encode("utf-8")


def _show_diff(git_path: str, before: str, after: str) -> None:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{git_path}",
        tofile=f"b/{git_path}",
    )
    for line in diff:
        print(line.rstrip("\r\n"))


def annotate_staged(model_name: str, dry_run: bool = False) -> int:
    root = _repo_root()
    index_snapshot = _git(
        "diff", "--cached", "--raw", "--no-abbrev", "-z", cwd=root
    ).stdout
    files = _staged_sources(root)
    if not files:
        print("No staged Python or Java files to annotate.")
        return 0

    os.environ["BTL_OLLAMA_MODEL"] = model_name
    proposed: list[FileChange] = []
    for git_path, language in files:
        path, original, source = _read_clean_file(root, git_path)
        result = annotate_source(source, "local", language)
        if not result.valid:
            raise GitCommandError(f"{git_path}: {result.status}")
        if result.notes:
            raise GitCommandError(f"{git_path}: {result.status}")
        updated = _proposed_bytes(original, result.annotated_source)
        if updated != original:
            proposed.append(FileChange(git_path, path, original, updated))

    if not proposed:
        print("No new comments needed in staged files.")
        return 0

    for change in proposed:
        _show_diff(
            change.git_path,
            change.original.decode("utf-8-sig"),
            change.updated.decode("utf-8-sig"),
        )
    if dry_run:
        print(f"Previewed {len(proposed)} file(s); no files were changed.")
        return 0

    # Model inference can take long enough for the user to edit or restage files.
    # Check every proposal before writing any of them.
    current_index = _git(
        "diff", "--cached", "--raw", "--no-abbrev", "-z", cwd=root
    ).stdout
    if current_index != index_snapshot:
        raise GitCommandError(
            "Staging area changed during annotation; review it and run again. No files were written."
        )
    for change in proposed:
        path = change.path
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root)
            or path.read_bytes() != change.original
        ):
            raise GitCommandError(
                f"{change.git_path}: file changed during annotation. No files were written."
            )

    for change in proposed:
        change.path.write_bytes(change.updated)
    print(
        f"Annotated {len(proposed)} file(s). Review the diff, then stage the comments with git add."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="git btl", description="Review local model comments before committing."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    annotate = commands.add_parser(
        "annotate", help="Annotate staged Python and Java files"
    )
    annotate.add_argument("--staged", action="store_true", required=True)
    annotate.add_argument(
        "--model", default=os.getenv("BTL_OLLAMA_MODEL", DEFAULT_LOCAL_MODEL)
    )
    annotate.add_argument(
        "--dry-run", action="store_true", help="Preview comments without changing files"
    )
    args = parser.parse_args(argv)
    try:
        return annotate_staged(args.model, args.dry_run)
    except (GitCommandError, ModelUnavailableError, OSError) as exc:
        print(f"git btl: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

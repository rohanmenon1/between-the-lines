import argparse
import sys
from pathlib import Path

from .annotate import annotate_python
from .model import ModelUnavailableError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="between-the-lines",
        description="Annotate a Python file with model-generated comments and verify the semantic AST is unchanged.",
    )
    parser.add_argument("input", type=Path, help="Python file to annotate.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write annotated Python to this file. Defaults to stdout.",
    )
    parser.add_argument(
        "--model",
        choices=["base", "tuned"],
        default="base",
        help="Comment model to use. Defaults to base.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input file with the annotated version after validation.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Parse and validate only; do not write annotated code.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print the file summary and validation status to stderr.",
    )
    return parser.parse_args()


def read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def main() -> int:
    args = parse_args()
    if args.output and args.in_place:
        print("error: use either --output or --in-place, not both", file=sys.stderr)
        return 2

    try:
        source = read_source(args.input)
    except OSError as exc:
        print(f"error: could not read {args.input}: {exc}", file=sys.stderr)
        return 2
    except UnicodeDecodeError as exc:
        print(f"error: could not decode {args.input} as UTF-8 Python text: {exc}", file=sys.stderr)
        return 2

    try:
        result = annotate_python(source, args.model)
    except ModelUnavailableError as exc:
        print(f"error: model unavailable: {exc}", file=sys.stderr)
        return 1
    if args.summary or args.check:
        if result.summary:
            print(result.summary, file=sys.stderr)
        print(result.status, file=sys.stderr)

    if not result.valid:
        return 1

    if args.check:
        return 0

    if args.in_place:
        args.input.write_text(result.annotated_source, encoding="utf-8")
        return 0

    if args.output:
        args.output.write_text(result.annotated_source, encoding="utf-8")
        return 0

    print(result.annotated_source, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

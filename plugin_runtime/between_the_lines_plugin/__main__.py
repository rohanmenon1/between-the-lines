"""Bundled entry point for trusted lifecycle hooks and local MCP tools."""

import argparse
import json
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--hook", choices=["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"])
    mode.add_argument("--mcp", action="store_true")
    mode.add_argument("--version", action="store_true")
    mode.add_argument("--prepare-install", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print("between-the-lines Codex plugin 0.1.0")
        return 0
    if args.prepare_install:
        from .install import prepare_install
        try:
            print(json.dumps(prepare_install(), ensure_ascii=True))
            return 0
        except (OSError, ValueError, KeyError, TypeError) as error:
            print("between-the-lines setup failed: " + str(error), file=sys.stderr)
            return 2
    if args.mcp:
        from .mcp import serve
        serve()
        return 0
    try:
        from .coordinator import Coordinator
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Hook input exceeds its limit.")
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("A hook event must be an object.")
        result = Coordinator().handle_hook(args.hook, event)
        print(json.dumps(result or {}, ensure_ascii=True))
    except Exception:
        # A failure should not prevent normal coding or invent captured context.
        print("between-the-lines skipped an unavailable hook operation.", file=sys.stderr)
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

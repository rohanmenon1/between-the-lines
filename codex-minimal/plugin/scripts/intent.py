"""Resolve direct Python calls against approved, Git-tracked intent."""

import ast
import json
from collections import Counter
from hashlib import sha256

from annotation import source_path


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
DEFINITIONS = (*FUNCTIONS, ast.ClassDef)


def parse(source):
    return ast.parse(source.decode("utf-8-sig"), type_comments=True)


def fingerprint(node):
    return "sha256:" + sha256(ast.dump(node).encode()).hexdigest()


def definitions(tree, prefix=""):
    for node in tree.body:
        if isinstance(node, DEFINITIONS):
            name = prefix + node.name
            yield name, node
            if isinstance(node, ast.ClassDef):
                yield from definitions(node, name + ".")


def body_nodes(function):
    """Walk the selected body, skipping separate function and class bodies."""
    pending = list(reversed(function.body))
    while pending:
        node = pending.pop()
        if isinstance(node, (*DEFINITIONS, ast.Lambda)):
            continue
        yield node
        pending.extend(reversed(list(ast.iter_child_nodes(node))))


def bindings(tree):
    """Count possible bindings conservatively, even in different scopes."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, DEFINITIONS):
            names.append(node.name)
        elif isinstance(node, ast.alias):
            names.append(node.asname or node.name.split(".")[0])
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.append(node.id)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            names.append(node.name)
        elif isinstance(node, ast.MatchMapping):
            names.append(node.rest)
    return Counter(names)


def resolve(root, relative, tree):
    counts = bindings(tree)
    if counts["*"]:
        return {}
    targets = {}
    for node in tree.body:
        if isinstance(node, DEFINITIONS):
            targets[node.name] = (relative, node.name)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            module = node.module.replace(".", "/")
            candidates = [p for p in (module + ".py", module + "/__init__.py")
                          if (root / p).is_file()]
            if len(candidates) == 1:
                for alias in node.names:
                    targets[alias.asname or alias.name] = (candidates[0], alias.name)
    return {name: target for name, target in targets.items() if counts[name] == 1}


def record(records, path, symbol, definition):
    item = {"path": path, "symbol": symbol, "status": "unresolved"}
    if definition is None:
        return item
    group = records.get(path, {})
    if not isinstance(group, dict):
        raise ValueError(f"Invalid intent map for {path}.")
    item.update(status="missing", ast_hash=fingerprint(definition))
    if symbol in group:
        entry = group[symbol]
        if (not isinstance(entry, dict) or not isinstance(entry.get("intent"), str)
                or not 0 < len(entry["intent"].strip()) <= 400
                or not isinstance(entry.get("ast_hash"), str)):
            raise ValueError(f"Invalid intent record for {path}:{symbol}.")
        item["status"] = "stale"
        if entry["ast_hash"] == item["ast_hash"]:
            item.update(status="current", intent=entry["intent"])
    return item


def lookup(root, relative, source, line=None, *, symbol=None):
    tree = parse(source)
    relative = source_path(root, relative).relative_to(root).as_posix()
    matches = [(name, node) for name, node in definitions(tree)
               if (name == symbol if symbol is not None else
                   isinstance(node, FUNCTIONS) and node.lineno == line)]
    if len(matches) != 1:
        raise ValueError("Select one unambiguous Python definition.")
    name, function = matches[0]
    file = root / "intent.json"
    if file.is_symlink():
        raise ValueError("intent.json must be a regular repository file.")
    try:
        with file.open("rb") as stream:
            raw = stream.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("intent.json exceeds 1 MiB.")
        records = json.loads(raw)
    except FileNotFoundError:
        records = {}
    if not isinstance(records, dict):
        raise ValueError("intent.json must map paths to symbol records.")

    targets = resolve(root, relative, tree)
    parsed = {relative: dict(definitions(tree))}
    calls = []
    seen = set()
    for node in body_nodes(function) if isinstance(function, FUNCTIONS) else ():
        if not isinstance(node, ast.Call):
            continue
        target = targets.get(node.func.id) if isinstance(node.func, ast.Name) else None
        key = target or ast.unparse(node.func)
        if key in seen:
            continue
        seen.add(key)
        if len(calls) == 32:
            break
        if target is None:
            calls.append({"call": str(key)[:120], "status": "unresolved"})
            continue
        path, symbol = target
        if path not in parsed:
            try:
                imported = parse(source_path(root, path).read_bytes())
                counts = bindings(imported)
                parsed[path] = {candidate: definition for candidate, definition in definitions(imported)
                                if counts[candidate] == 1 and not counts["*"]}
            except (OSError, ValueError, SyntaxError):
                parsed[path] = {}
        calls.append(record(records, path, symbol, parsed[path].get(symbol)))
    return {**record(records, relative, name, function), "calls": calls}

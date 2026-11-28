"""Rank reviewed Python symbols by word overlap, validating matching files on demand."""

import re

from annotation import source_path
from intent import FUNCTIONS, definitions, parse, read_records, record


STOP = set("a an and are as at by code does find for from function functions in into is me method of or please return returns that the to which with".split())


def terms(text):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    words = re.findall(r"[^\W_]+", text.casefold())
    return {word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss")
            else word for word in words if word not in STOP}


def search(root, query):
    wanted = terms(query)
    candidates = []
    records = read_records(root)
    for relative, group in records.items():
        if not isinstance(group, dict):
            continue
        matches = []
        for symbol, entry in group.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("intent"), str):
                continue
            names = terms(symbol)
            overlap = wanted & (names | terms(entry["intent"]))
            if overlap:
                matches.append((symbol, overlap, len(wanted & names)))
        if not matches:
            continue
        try:
            file = source_path(root, relative)
            nodes = {}
            for name, node in definitions(parse(file.read_bytes())):
                nodes[name] = None if name in nodes else node
        except (ValueError, OSError, SyntaxError, RecursionError):
            continue
        for symbol, overlap, name_hits in matches:
            node = nodes.get(symbol)
            if not isinstance(node, FUNCTIONS):
                continue
            checked = record(records, relative, symbol, node)
            if checked["status"] == "current":
                item = {"path": str(file), "symbol": symbol, "line": node.lineno,
                        "intent": checked["intent"], "matched_terms": sorted(overlap)}
                candidates.append(((-len(overlap), -name_hits, relative, symbol), item))
    candidates.sort(key=lambda pair: pair[0])
    return {"candidates": [item for _, item in candidates[:5]]}

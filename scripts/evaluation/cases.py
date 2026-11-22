"""Validate and freeze synthetic development cases before model inference."""

import hashlib
import json
from pathlib import Path
import re

from btl_agent.parsing import discover


CONTAINER_KINDS = {"class", "interface", "enum", "record", "annotation"}


def select_declaration(case):
    """Require a unique target instead of choosing whichever declaration is first."""
    declarations = discover(case["source"], case["language"])
    requested = case.get("symbolId", case.get("symbol"))
    if requested is not None:
        candidates = [item for item in declarations if item.symbol_id == requested]
        if not candidates:
            candidates = [item for item in declarations if item.name == requested]
    else:
        candidates = [item for item in declarations if item.kind not in CONTAINER_KINDS]
        candidates = candidates or declarations
    if len(candidates) != 1:
        raise ValueError(f"Case {case['id']}: choose a unique symbolId; found {len(candidates)} matching declarations.")
    return candidates[0]


def load_cases(path, case_ids=None, limit=None):
    """Keep reviewer-only labels in the snapshot, never in a model payload."""
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    if len(raw) > 5_000_000:
        raise ValueError("The evaluation case file exceeds 5 MB.")
    data = json.loads(raw.decode("utf-8-sig"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError("Provide between one and 100 evaluation cases.")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Every evaluation case must be an object.")
        identifier = case.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", identifier):
            raise ValueError("Case IDs must use lowercase letters, digits, underscores or hyphens.")
        if identifier in seen:
            raise ValueError(f"Duplicate case ID: {identifier}")
        seen.add(identifier)
        if case.get("language") not in {"python", "java"}:
            raise ValueError(f"Case {identifier}: supported languages are Python and Java.")
        if not isinstance(case.get("source"), str) or not 0 < len(case["source"].encode("utf-8")) <= 131072:
            raise ValueError(f"Case {identifier}: provide at most 128 KiB of source.")
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip() or len(case["prompt"]) > 16000:
            raise ValueError(f"Case {identifier}: provide a nonempty task of at most 16,000 characters.")
        if "previousSource" in case and not isinstance(case["previousSource"], str):
            raise ValueError(f"Case {identifier}: previousSource must be source text.")
        if "knownRequirements" in case and (not isinstance(case["knownRequirements"], list)
                or not all(isinstance(record, dict) for record in case["knownRequirements"])):
            raise ValueError(f"Case {identifier}: knownRequirements must be a list of records.")
        for field in ("symbol", "symbolId"):
            if field in case and (not isinstance(case[field], str) or not case[field]):
                raise ValueError(f"Case {identifier}: {field} must be a nonempty string.")
        select_declaration(case)
    if case_ids:
        unknown = set(case_ids) - seen
        if unknown:
            raise ValueError("Unknown case IDs: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case["id"] in set(case_ids)]
    if limit is not None:
        if type(limit) is not int or limit < 1:
            raise ValueError("The case limit must be a positive integer.")
        cases = cases[:limit]
    return {"sha256": hashlib.sha256(raw).hexdigest(), "path": str(path),
            "description": data.get("description", "Synthetic development cases") if isinstance(data, dict) else "Synthetic development cases",
            "kind": "synthetic_development", "cases": cases}

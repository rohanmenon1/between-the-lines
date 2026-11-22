"""Check original prompt evidence separately from model-written comments."""

import re
import unicodedata

from .types import WorkerError


CONTEXT_PREFIX = "User context: "
# patches.py permits 240 characters before adding a language comment prefix.
MAX_EXCERPT_LENGTH = 240 - len(CONTEXT_PREFIX)


def _continues_requirement(line):
    if re.match(r"^(?:if|when|unless|except|only if|only when|but|provided that|as long as|for|with|without|while|until|assuming)\b", line, re.I):
        return True
    independent = re.match(r"^(?:[-*]\s+)?`?[\w.]+`?(?:\(\))?\s+(?:must|shall|should|needs to|has to|is required to)\b", line, re.I)
    return bool(line and line[0].islower() and not independent)


def prompt_evidence(quote, prompt):
    """Locate complete source lines without claiming their semantic support.

    Models may normalize CRLF to LF when quoting. Map those line endings back
    to the original source before returning offsets and the immutable passage.
    Comments are checked elsewhere; they need not reproduce this passage.
    """
    if (not isinstance(prompt, str) or not isinstance(quote, str) or not quote
            or len(quote) > 2000 or quote != quote.strip()
            or any(unicodedata.category(char).startswith("C") and char not in "\r\n\t" for char in quote)):
        raise WorkerError("unsupported_evidence", "Select a complete original prompt passage of at most 2,000 characters.")
    normalized, offsets = [], []
    index = 0
    while index < len(prompt):
        offsets.append(index)
        char = prompt[index]
        normalized.append("\n" if char == "\r" else char)
        index += 2 if prompt[index:index + 2] == "\r\n" else 1
    offsets.append(len(prompt))
    normalized = "".join(normalized)
    wanted = quote.replace("\r\n", "\n").replace("\r", "\n")
    position = 0
    while (position := normalized.find(wanted, position)) != -1:
        start, end = offsets[position], offsets[position + len(wanted)]
        line_start = max(prompt.rfind("\n", 0, start), prompt.rfind("\r", 0, start)) + 1
        following = [item for marker in ("\r", "\n") if (item := prompt.find(marker, end)) != -1]
        line_end = min(following, default=len(prompt))
        if not prompt[line_start:start].strip() and not prompt[end:line_end].strip():
            original = prompt[start:end]
            following_lines = prompt[end:].splitlines()
            after = following_lines[1].strip() if len(following_lines) > 1 else ""
            previous_lines = prompt[:line_start].splitlines()
            before = previous_lines[-1].strip() if previous_lines else ""
            clipped = _continues_requirement(after) or before.endswith(":") or bool(
                re.match(r"^(?:if|unless|when|except|only if|only when|provided that)\b", before, re.I))
            if len(original) <= 2000 and not clipped:
                return {"quote": original,
                        "start": len(prompt[:start].encode("utf-16-le")) // 2,
                        "end": len(prompt[:end].encode("utf-16-le")) // 2}
        position += 1
    raise WorkerError("unsupported_evidence", "The evidence is not a complete, verbatim original prompt passage.")


def evidence_choices(prompt):
    """Offer exact source passages for selection instead of transcription."""
    if not isinstance(prompt, str):
        return []
    lines = list(prompt_passages(prompt))
    spans = []
    paragraph_start = previous = None
    for item in lines:
        gap = prompt[previous["end"]:item["start"]] if previous else ""
        if previous and (gap.strip() or gap.count("\n") > 1):
            spans.append((paragraph_start["start"], previous["end"]))
            paragraph_start = None
        paragraph_start = paragraph_start or item
        previous = item
        if len(spans) >= 64:
            break
    if paragraph_start and previous:
        spans.append((paragraph_start["start"], previous["end"]))
    spans.extend((item["start"], item["end"]) for item in lines[:64])
    # Adjacent lines can carry a wrapped qualifier. Include complete contiguous
    # passages so the model can retain that wording without counting offsets.
    for start_index, first in enumerate(lines[:64]):
        for last in lines[start_index + 1:start_index + 8]:
            if last["end"] - first["start"] > 2000:
                break
            span = prompt[first["start"]:last["end"]]
            if "```" in span or "~~~" in span or any(line.lstrip().startswith(">") for line in span.splitlines()):
                break
            spans.append((first["start"], last["end"]))
            if len(spans) >= 256:
                break
        if len(spans) >= 256:
            break
    choices, seen = [], set()
    for start, end in spans[:256]:
        try:
            evidence = prompt_evidence(prompt[start:end], prompt)
        except WorkerError:
            continue
        if evidence["quote"] in seen:
            continue
        seen.add(evidence["quote"])
        choices.append({"id": "p" + str(len(choices)), "quote": evidence["quote"]})
        if len(choices) >= 32:
            break
    return choices


def prompt_passages(prompt):
    """Keep source offsets while excluding fenced code and Markdown quotations."""
    fence = None
    offset = 0
    for raw in prompt.splitlines(keepends=True):
        line = raw.strip()
        start = offset + len(raw) - len(raw.lstrip())
        offset += len(raw)
        marker = re.match(r"^(`{3,}|~{3,})", line)
        if marker:
            current = marker.group(1)[0]
            if fence is None:
                fence = current
            elif current == fence:
                fence = None
            continue
        if fence is None and line and not line.startswith(">"):
            yield {"quote": line, "start": start, "end": start + len(line)}


def _prompt_lines(prompt):
    return (passage["quote"] for passage in prompt_passages(prompt))


def automatic_passages(prompt, symbol_name):
    """Select only standalone, explicit requirements for automatic attribution.

    This is a deliberately narrow syntax rule, not an intent classifier. Longer
    discussions, questions and examples remain available for manual review.
    """
    if not isinstance(prompt, str) or not isinstance(symbol_name, str):
        return []
    reference = r"`?" + re.escape(symbol_name) + r"`?(?:\(\))?"
    requirement = re.compile(
        r"^(?:[-*]\s+)?" + reference
        + r"\s+(?:must|shall|should|needs to|has to|is required to)\b",
        re.IGNORECASE,
    )
    independent_requirement = re.compile(
        r"^(?:[-*]\s+)?`?[\w.]+`?(?:\(\))?"
        r"\s+(?:must|shall|should|needs to|has to|is required to)\b", re.I,
    )
    # A passage must stand alone. Quoting part of a discussion, rejected option,
    # conditional preamble or a Markdown code example would imply extra scope.
    discussion = re.compile(
        r"\b(?:example|hypothetical|alternative|rejected|previously|used to)\b",
        re.IGNORECASE,
    )
    result = []
    passages = list(prompt_passages(prompt))
    physical_lines = prompt.splitlines()
    for passage in passages:
        quote = passage["quote"]
        if not requirement.match(quote) or discussion.search(quote) or quote.endswith("?"):
            continue
        # A whole line does not necessarily contain a whole condition. Reject
        # following/preceding continuation text that cannot be attributed alone.
        number = prompt[:passage["start"]].count("\n")
        before = physical_lines[number - 1].strip() if number else ""
        after = physical_lines[number + 1].strip() if number + 1 < len(physical_lines) else ""
        continuation = re.match(
            r"^(?:if|when|unless|except|only if|only when|but|provided that|as long as|for|with|without|while|until|assuming)\b",
            after, re.I,
        )
        # A wrapped sentence commonly continues with a lowercase word. Keep
        # independent symbol requirements on adjacent lines eligible, but do
        # not lift the first line out of an apparent continuation.
        lowercase_continuation = after and after[0].islower() and not independent_requirement.match(after)
        if (before.endswith(":") or re.match(r"^(?:if|unless|when|except|only if|only when|provided that)\b", before, re.I)
                or continuation or lowercase_continuation):
            continue
        try:
            build_grounded_comment({"comment": quote}, prompt, symbol_name)
        except WorkerError:
            continue
        result.append(passage)
    # Repetition is harmless; competing instructions are not automatically
    # resolved by order or a model's interpretation.
    return list({item["quote"]: item for item in result}.values())


def build_grounded_comment(decision, prompt, symbol_name=None):
    """Return an attributed complete prompt line, or abstain with an error.

    This establishes that the displayed words occurred in the supplied prompt.
    It does not establish whether they are an active requirement, correctly
    scoped, or implemented. Automatic eligibility is checked separately;
    model-driven suggestions require user review.
    """
    comment = decision.get("comment") if isinstance(decision, dict) else None
    if not isinstance(prompt, str) or not isinstance(comment, str):
        raise WorkerError("unsupported_intent", "A comment requires an exact prompt passage.")
    if not comment or len(comment) > MAX_EXCERPT_LENGTH or comment != comment.strip():
        raise WorkerError("unsupported_intent", "Select a complete prompt line that fits in one comment.")
    if any(unicodedata.category(char).startswith("C") for char in comment):
        raise WorkerError("unsupported_intent", "The prompt passage contains unsupported control characters.")
    if comment not in _prompt_lines(prompt):
        raise WorkerError("unsupported_intent", "The proposal is not a complete, verbatim prompt line.")
    if symbol_name is not None:
        if not isinstance(symbol_name, str) or not symbol_name or not re.search(
            r"(?<!\w)" + re.escape(symbol_name) + r"(?!\w)", comment
        ):
            raise WorkerError("unsupported_intent", "The prompt passage must name this declaration explicitly.")
    return CONTEXT_PREFIX + comment

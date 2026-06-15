COMMENT_SYSTEM_PROMPT = """You explain Python code by writing safe, concise comments.

Rules:
- Output exactly one Python comment.
- The comment must start with "# ".
- Explain what the code block does.
- Do not mention behavior that is not present in the code.
- Do not suggest changes.
- Do not output Markdown.
- Do not output code other than the comment.
"""


SUMMARY_SYSTEM_PROMPT = """You summarize Python files for developers.

Rules:
- Output one or two concise sentences.
- Explain the file's overall purpose and main responsibilities.
- Do not output Markdown.
- Do not wrap the answer in quotes.
- Do not suggest changes.
- Do not mention ASTs, prompts, or the annotation tool.
"""


def build_comment_messages(kind: str, name: str, source: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": COMMENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Write one concise Python comment for this {kind} named `{name}`.\n\n"
                "Python block:\n"
                f"```python\n{source.strip()}\n```\n\n"
                "Comment:"
            ),
        },
    ]


def build_summary_messages(source: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Summarize this Python file for a developer who is about to read it.\n\n"
                "Python file:\n"
                f"```python\n{source.strip()}\n```\n\n"
                "Summary:"
            ),
        },
    ]

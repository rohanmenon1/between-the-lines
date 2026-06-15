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

COMMENT_SYSTEM_PROMPT = """You explain source code by writing safe, concise comments.

Rules:
- Output exactly one line comment in the requested language.
- Explain what the code block does.
- Do not mention behavior that is not present in the code.
- Do not suggest changes.
- Do not output Markdown.
- Do not output code other than the comment.
"""


SUMMARY_SYSTEM_PROMPT = """You summarize source files for developers.

Rules:
- Output one or two concise sentences.
- Explain the file's overall purpose and main responsibilities.
- Do not output Markdown.
- Do not wrap the answer in quotes.
- Do not suggest changes.
- Do not mention ASTs, prompts, or the annotation tool.
"""


def build_comment_messages(kind: str, name: str, source: str, language: str = "python") -> list[dict[str, str]]:
    prefix = "# " if language == "python" else "// "
    return [
        {"role": "system", "content": COMMENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Write one concise {language} comment starting with `{prefix}` for this {kind} named `{name}`.\n\n"
                f"{language.title()} block:\n"
                f"```{language}\n{source.strip()}\n```\n\n"
                "Comment:"
            ),
        },
    ]


def build_summary_messages(source: str, language: str = "python") -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Summarize this {language} file for a developer who is about to read it.\n\n"
                f"{language.title()} file:\n"
                f"```{language}\n{source.strip()}\n```\n\n"
                "Summary:"
            ),
        },
    ]

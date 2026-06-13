import ast
import html
import textwrap
from dataclasses import dataclass

import gradio as gr


EXAMPLE_CODE = """\
from pathlib import Path


def load_names(path: str) -> list[str]:
    rows = Path(path).read_text().splitlines()
    return [row.strip() for row in rows if row.strip()]


class Greeter:
    def __init__(self, names: list[str]):
        self.names = names

    def messages(self) -> list[str]:
        return [f"Hello, {name}!" for name in self.names]
"""


CSS = """
:root {
  --btl-bg: #f4f8f3;
  --btl-panel: #ffffff;
  --btl-ink: #111812;
  --btl-muted: #5b6a5f;
  --btl-line: #d8e4d6;
  --btl-green: #1f6f43;
  --btl-green-dark: #0d2818;
  --btl-code-bg: #07110b;
}

.gradio-container {
  background:
    linear-gradient(180deg, rgba(13, 40, 24, 0.08), transparent 240px),
    var(--btl-bg) !important;
  color: var(--btl-ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

#btl-shell {
  max-width: 1280px;
  margin: 0 auto;
}

#btl-title {
  padding: 22px 0 10px;
}

#btl-title h1 {
  color: var(--btl-green-dark);
  font-size: clamp(2rem, 5vw, 4.75rem);
  line-height: 0.96;
  letter-spacing: 0;
  margin: 0;
}

#btl-title p {
  color: var(--btl-muted);
  font-size: 1.02rem;
  max-width: 780px;
  margin: 14px 0 0;
}

.btl-card,
.form,
.block {
  border-color: var(--btl-line) !important;
  border-radius: 8px !important;
}

button.primary,
.primary > button {
  background: var(--btl-green-dark) !important;
  border-color: var(--btl-green-dark) !important;
  color: #f8fff8 !important;
  border-radius: 8px !important;
}

button.secondary,
.secondary > button {
  border-radius: 8px !important;
}

textarea,
pre,
code {
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace !important;
}

#status_box textarea,
#summary_box textarea {
  background: #fbfdf9 !important;
}

#input_code textarea,
#output_code textarea {
  min-height: 520px !important;
  line-height: 1.45 !important;
}

#output_code textarea {
  background: var(--btl-code-bg) !important;
  color: #dbf8df !important;
}
"""


@dataclass(frozen=True)
class BlockInfo:
    kind: str
    name: str
    lineno: int


def _parse_python(source: str) -> ast.Module:
    return ast.parse(source)


def _strip_docstrings(node: ast.AST) -> ast.AST:
    copied = ast.fix_missing_locations(ast.parse(ast.unparse(node)))
    for child in ast.walk(copied):
        if isinstance(child, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                child.body
                and isinstance(child.body[0], ast.Expr)
                and isinstance(child.body[0].value, ast.Constant)
                and isinstance(child.body[0].value.value, str)
            ):
                child.body = child.body[1:]
    return copied


def _semantic_ast_dump(source: str) -> str:
    tree = _parse_python(source)
    stripped = _strip_docstrings(tree)
    return ast.dump(stripped, include_attributes=False)


def _collect_blocks(tree: ast.Module) -> list[BlockInfo]:
    blocks: list[BlockInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            blocks.append(BlockInfo("class", node.name, node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            blocks.append(BlockInfo(kind, node.name, node.lineno))
    return sorted(blocks, key=lambda item: item.lineno)


def _placeholder_summary(blocks: list[BlockInfo]) -> str:
    if not blocks:
        return "This Python file has no classes or functions to annotate yet."
    names = ", ".join(f"{block.kind} `{block.name}`" for block in blocks[:8])
    overflow = "" if len(blocks) <= 8 else f", plus {len(blocks) - 8} more"
    return f"This file defines {names}{overflow}. Model-generated summaries will replace this deterministic placeholder."


def _insert_placeholder_comments(source: str, blocks: list[BlockInfo]) -> str:
    lines = source.splitlines()
    inserts: dict[int, list[str]] = {}
    for block in blocks:
        indent = len(lines[block.lineno - 1]) - len(lines[block.lineno - 1].lstrip())
        comment = " " * indent + f"# TODO(model): Explain the {block.kind} `{block.name}` without changing code behavior."
        inserts.setdefault(block.lineno - 1, []).append(comment)

    annotated: list[str] = []
    for index, line in enumerate(lines):
        annotated.extend(inserts.get(index, []))
        annotated.append(line)
    return "\n".join(annotated) + ("\n" if source.endswith("\n") else "")


def annotate_code(source: str) -> tuple[str, str, str]:
    source = source.strip("\ufeff")
    if not source.strip():
        return "", "", "Paste a Python file to annotate."

    try:
        original_tree = _parse_python(source)
    except SyntaxError as exc:
        return "", "", f"Syntax error on line {exc.lineno}: {html.escape(exc.msg)}"

    blocks = _collect_blocks(original_tree)
    annotated = _insert_placeholder_comments(source, blocks)

    try:
        same_ast = _semantic_ast_dump(source) == _semantic_ast_dump(annotated)
    except SyntaxError as exc:
        return "", annotated, f"Generated annotation failed to parse on line {exc.lineno}: {html.escape(exc.msg)}"

    status = (
        f"Validated: parsed {len(blocks)} block(s), inserted placeholder comments, semantic AST unchanged."
        if same_ast
        else "Rejected: annotation changed the semantic AST."
    )
    return _placeholder_summary(blocks), annotated if same_ast else source, status


def build_app() -> gr.Blocks:
    with gr.Blocks(
        css=CSS,
        title="between-the-lines",
        theme=gr.themes.Base(
            primary_hue="green",
            neutral_hue="zinc",
            radius_size="sm",
            font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui"],
            font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "Consolas", "monospace"],
        ),
    ) as demo:
        with gr.Column(elem_id="btl-shell"):
            gr.HTML(
                """
                <header id="btl-title">
                  <h1>between-the-lines</h1>
                  <p>Upload or paste a Python file, then generate comments that are checked against the file's AST before they are shown.</p>
                </header>
                """
            )

            with gr.Row(equal_height=True):
                with gr.Column(scale=1):
                    input_code = gr.Code(
                        label="Original Python",
                        language="python",
                        value=EXAMPLE_CODE,
                        lines=24,
                        elem_id="input_code",
                    )
                    with gr.Row():
                        run_button = gr.Button("Annotate", variant="primary")
                        gr.ClearButton(value="Clear", components=[input_code])
                with gr.Column(scale=1):
                    output_code = gr.Code(
                        label="Annotated Python",
                        language="python",
                        lines=24,
                        elem_id="output_code",
                    )

            with gr.Row(equal_height=True):
                summary = gr.Textbox(label="File Summary", lines=4, elem_id="summary_box")
                status = gr.Textbox(label="Validation", lines=4, elem_id="status_box")

            run_button.click(
                annotate_code,
                inputs=input_code,
                outputs=[summary, output_code, status],
            )

            gr.Examples(
                examples=[[textwrap.dedent(EXAMPLE_CODE)]],
                inputs=input_code,
                label="Example",
            )

    return demo


demo = build_app()


if __name__ == "__main__":
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=False,
    )

import html
from pathlib import Path
from typing import Any

import gradio as gr

from btl.annotate import annotate_python, collect_blocks, parse_python
from btl.model import ModelUnavailableError

try:
    import spaces
except ImportError:
    spaces = None


def zero_gpu_task(fn):
    if spaces is None:
        return fn
    return spaces.GPU(duration=180)(fn)


CSS = """
:root {
  --btl-bg: #f3f8f0;
  --btl-panel: #ffffff;
  --btl-ink: #111812;
  --btl-muted: #5b6a5f;
  --btl-line: #d8e4d6;
  --btl-green: #1f6f43;
  --btl-green-dark: #0d2818;
  --btl-green-soft: #e3f1df;
  --btl-code-bg: #07110b;
}

.gradio-container {
  background:
    radial-gradient(circle at 18% 0%, rgba(31, 111, 67, 0.16), transparent 28rem),
    linear-gradient(135deg, rgba(7, 17, 11, 0.08), transparent 24rem),
    linear-gradient(180deg, rgba(13, 40, 24, 0.08), transparent 260px),
    var(--btl-bg) !important;
  color: var(--btl-ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

#btl-shell {
  max-width: 1280px;
  margin: 0 auto;
}

#btl-title {
  padding: 22px 0 12px;
  text-align: center;
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
  line-height: 1.6;
  max-width: 850px;
  margin: 14px auto 0;
}

#btl-cli {
  max-width: 780px;
  margin: 0 auto 22px;
}

#btl-cli .btl-cli-label {
  color: var(--btl-muted);
  font-size: 0.82rem;
  font-weight: 750;
  letter-spacing: 0;
  margin: 0 0 7px;
  text-transform: uppercase;
}

#btl-cli pre {
  background: #f1f3f5;
  border: 1px solid #d5d9dd;
  border-radius: 8px;
  color: #18201b;
  margin: 0;
  overflow-x: auto;
  padding: 14px 16px;
  text-align: left;
}

#btl-cli code {
  color: #18201b;
  font-size: 0.94rem;
  white-space: pre;
}

.btl-upload .wrap {
  background: rgba(255, 255, 255, 0.76) !important;
}

#btl-controls,
#btl-actions {
  justify-content: center;
  margin-bottom: 12px;
}

#btl-controls {
  align-items: end;
}

#btl-actions {
  align-items: center;
}

#btl-controls > .form,
#btl-controls > .block {
  min-height: 72px !important;
}

#btl-controls > *,
#btl-actions > * {
  flex: 0 1 360px !important;
}

#btl-actions > * {
  max-width: 190px;
}

#model_choice label,
#input_code label,
#output_code label,
#summary_box label,
#status_box label {
  color: var(--btl-green-dark) !important;
  font-weight: 700 !important;
}

.btl-card,
.form:not(#input_code):not(#output_code),
.block:not(#input_code):not(#output_code) {
  border-color: var(--btl-line) !important;
  border-radius: 8px !important;
  box-shadow: 0 10px 35px rgba(7, 17, 11, 0.05) !important;
}

#input_code,
#output_code,
#input_code > .block,
#output_code > .block,
#input_code .form,
#output_code .form,
#input_code .wrap,
#output_code .wrap {
  border-color: transparent !important;
  box-shadow: none !important;
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

#summary_box textarea {
  background: #f7fbf4 !important;
  color: var(--btl-green-dark) !important;
  text-align: center !important;
  font-family: Inter, ui-sans-serif, system-ui, sans-serif !important;
  font-weight: 650 !important;
  line-height: 1.55 !important;
}

#status_box textarea {
  background: #123923 !important;
  color: #e7f8e8 !important;
  border-color: #123923 !important;
  font-family: Inter, ui-sans-serif, system-ui, sans-serif !important;
  line-height: 1.45 !important;
}

#input_code textarea,
#output_code textarea {
  height: min(56vh, 620px) !important;
  line-height: 1.45 !important;
  overflow: auto !important;
}

#input_code .cm-editor,
#output_code .cm-editor {
  height: min(56vh, 620px) !important;
  max-height: 620px !important;
  border-top: 0 !important;
}

#input_code .cm-scroller,
#output_code .cm-scroller {
  height: min(56vh, 620px) !important;
  max-height: 620px !important;
  overflow: auto !important;
}

#output_code textarea {
  background: var(--btl-code-bg) !important;
  color: #dbf8df !important;
}

#output_code .cm-editor {
  background: var(--btl-code-bg) !important;
}

#output_code .cm-content,
#output_code .cm-gutters {
  background: var(--btl-code-bg) !important;
  color: #dbf8df !important;
}

#input_code .label-wrap,
#output_code .label-wrap,
#input_code .wrap,
#output_code .wrap {
  border: 0 !important;
  border-top-color: transparent !important;
  box-shadow: none !important;
}
"""


MODEL_LABELS = {
    "Base Mellum2 (richer)": "base",
    "Fine-tuned LoRA (concise)": "tuned",
}


@zero_gpu_task
def annotate_code(source: str, model_label: str) -> tuple[str, str, str]:
    try:
        result = annotate_python(source, MODEL_LABELS.get(model_label, "base"))
    except ModelUnavailableError as exc:
        return "", source, f"Model unavailable: {html.escape(str(exc))}"
    return result.summary, result.annotated_source, result.status


def load_uploaded_python(file_obj: Any) -> tuple[str, str]:
    if file_obj is None:
        return "", "Choose a `.py` file to load it into the editor."

    path = Path(file_obj if isinstance(file_obj, str) else file_obj.name)
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            source = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            return "", f"Could not read `{path.name}` as UTF-8 Python text: {exc}"
    except OSError as exc:
        return "", f"Could not read `{path.name}`: {exc}"

    try:
        tree = parse_python(source)
    except SyntaxError as exc:
        return source, f"Loaded `{path.name}`, but parsing failed on line {exc.lineno}: {html.escape(exc.msg)}"

    blocks = collect_blocks(tree, source)
    return source, f"Loaded `{path.name}`. Parsed {len(blocks)} class/function block(s)."


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
                  <p>Annotate Python files from the web or terminal with semantically meaningful comments. The model comments only on AST-backed code blocks, and the application deterministically verifies that the executable AST is unchanged after annotation.</p>
                </header>
                <section id="btl-cli" aria-label="Terminal command">
                  <p class="btl-cli-label">Run from your terminal</p>
                  <pre><code>npx between-the-lines-cli path/to/file.py --model base --summary</code></pre>
                </section>
                """
            )

            with gr.Row(equal_height=False, elem_id="btl-controls"):
                upload_file = gr.File(
                    label="Upload Python File",
                    file_types=[".py"],
                    elem_classes=["btl-upload"],
                    scale=2,
                )
                model_choice = gr.Dropdown(
                    label="Comment Model",
                    choices=list(MODEL_LABELS),
                    value="Base Mellum2 (richer)",
                    interactive=True,
                    elem_id="model_choice",
                    scale=2,
                )

            with gr.Row(equal_height=False, elem_id="btl-actions"):
                run_button = gr.Button("Annotate", variant="primary", scale=1)
                clear_button = gr.ClearButton(value="Clear", components=[], scale=1)

            with gr.Row(equal_height=True):
                with gr.Column(scale=1):
                    input_code = gr.Code(
                        label="Original Python",
                        language="python",
                        value="",
                        lines=24,
                        elem_id="input_code",
                    )
                with gr.Column(scale=1):
                    output_code = gr.Code(
                        label="Annotated Python",
                        language="python",
                        lines=24,
                        elem_id="output_code",
                    )

            with gr.Row(equal_height=True):
                summary = gr.Textbox(label="File Summary", lines=3, elem_id="summary_box")
                status = gr.Textbox(label="Validation", lines=3, elem_id="status_box")

            clear_button.add([input_code, output_code, summary, status])

            run_button.click(
                annotate_code,
                inputs=[input_code, model_choice],
                outputs=[summary, output_code, status],
                api_name="annotate",
            )
            upload_file.upload(
                load_uploaded_python,
                inputs=upload_file,
                outputs=[input_code, status],
            )

    return demo


demo = build_app()


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        inbrowser=False,
    )

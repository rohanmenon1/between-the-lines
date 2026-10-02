import html
from pathlib import Path
from typing import Any

import gradio as gr

from btl.annotate import annotate_python, annotate_source, collect_blocks, parse_python
from btl.java import collect_java_blocks, parse_java
from btl.model import ModelUnavailableError

try:
    import spaces
except ImportError:
    spaces = None


def zero_gpu_task(fn):
    if spaces is None:
        return fn
    return spaces.GPU(duration=120)(fn)


CSS = (Path(__file__).parent / "assets" / "style.css").read_text(encoding="utf-8")


MODEL_LABELS = {
    "Base Mellum2 (richer)": "base",
    "Fine-tuned LoRA (concise)": "tuned",
}


def cli_markup(language_label: str) -> str:
    filename = "File.java" if language_label == "Java" else "file.py"
    return (
        '<section id="btl-cli" aria-label="Terminal command">'
        '<p class="btl-cli-label">Run from your terminal</p>'
        f'<pre><code>npx between-the-lines-cli path/to/{filename} --model base --summary</code></pre>'
        '</section>'
    )


@zero_gpu_task
def annotate_code(source: str, model_label: str) -> tuple[str, str, str]:
    try:
        result = annotate_python(source, MODEL_LABELS.get(model_label, "base"))
    except ModelUnavailableError as exc:
        return "", source, f"Model unavailable: {html.escape(str(exc))}"
    return result.summary, result.annotated_source, result.status


@zero_gpu_task
def annotate_code_with_language(source: str, model_label: str, language_label: str) -> tuple[str, str, str]:
    try:
        result = annotate_source(source, MODEL_LABELS.get(model_label, "base"), language_label.lower())
    except ModelUnavailableError as exc:
        return "", source, f"Model unavailable: {html.escape(str(exc))}"
    except ImportError as exc:
        return "", source, f"Java support unavailable: install the Tree-sitter dependencies ({html.escape(str(exc))})"
    return result.summary, result.annotated_source, result.status


def load_uploaded_source(file_obj: Any) -> tuple[str, str, str, str, str]:
    if file_obj is None:
        return "", "", "", "Choose a `.py` or `.java` file to load it into the editor.", "Python"

    path = Path(file_obj if isinstance(file_obj, str) else file_obj.name)
    language = "Java" if path.suffix.lower() == ".java" else "Python"
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            source = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            return "", "", "", f"Could not read `{path.name}` as UTF-8 text: {exc}", language
    except OSError as exc:
        return "", "", "", f"Could not read `{path.name}`: {exc}", language

    try:
        if language == "Java":
            tree = parse_java(source)
            blocks = collect_java_blocks(tree, source)
        else:
            tree = parse_python(source)
            blocks = collect_blocks(tree, source)
    except SyntaxError as exc:
        detail = f"line {exc.lineno}: {exc.msg}" if exc.lineno else str(exc)
        return source, "", "", f"Loaded `{path.name}`, but parsing failed: {html.escape(detail)}", language
    except ImportError as exc:
        return source, "", "", f"Loaded `{path.name}`, but Java support is unavailable: install the Tree-sitter dependencies ({html.escape(str(exc))})", language

    return source, "", "", f"Loaded `{path.name}`. Parsed {len(blocks)} declaration(s).", language


def update_language(language_label: str):
    is_java = language_label == "Java"
    return (
        gr.update(language=None if is_java else "python", label=f"Original {language_label}"),
        gr.update(language=None if is_java else "python", label=f"Annotated {language_label}"),
        gr.update(
            choices=["Base Mellum2 (richer)"] if is_java else list(MODEL_LABELS),
            value="Base Mellum2 (richer)",
        ),
        cli_markup(language_label),
        "",
        "",
    )


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
                  <p>Annotate Python and Java files from the web or terminal. The model proposes comments for parsed declarations, and the app checks that the code structure is unchanged.</p>
                </header>
                """
            )
            cli_command = gr.HTML(cli_markup("Python"))

            with gr.Row(equal_height=False, elem_id="btl-controls"):
                upload_file = gr.File(
                    label="Upload Python or Java File",
                    file_types=[".py", ".java"],
                    elem_classes=["btl-upload"],
                    scale=2,
                )
                language_choice = gr.Dropdown(
                    label="Language",
                    choices=["Python", "Java"],
                    value="Python",
                    interactive=True,
                    elem_id="language_choice",
                    scale=1,
                )
                model_choice = gr.Dropdown(
                    label="Comment Model",
                    choices=list(MODEL_LABELS),
                    value="Base Mellum2 (richer)",
                    interactive=True,
                    elem_id="model_choice",
                    scale=1,
                )

            with gr.Row(equal_height=False, elem_id="btl-actions"):
                run_button = gr.Button("Annotate", variant="primary", scale=1)
                clear_button = gr.ClearButton(value="Clear", components=[], scale=1)
                legacy_api_button = gr.Button(visible=False)

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
                annotate_code_with_language,
                inputs=[input_code, model_choice, language_choice],
                outputs=[summary, output_code, status],
                api_name="annotate_multilang",
            )
            # Existing published CLI versions still call this two-argument API.
            legacy_api_button.click(
                annotate_code,
                inputs=[input_code, model_choice],
                outputs=[summary, output_code, status],
                api_name="annotate",
            )
            upload_file.upload(
                load_uploaded_source,
                inputs=upload_file,
                outputs=[input_code, output_code, summary, status, language_choice],
            )
            language_choice.change(
                update_language,
                inputs=language_choice,
                outputs=[input_code, output_code, model_choice, cli_command, summary, status],
            )
            language_choice.input(
                lambda: ("", "", ""),
                outputs=[output_code, summary, status],
            )
            input_code.input(
                lambda: ("", "", ""),
                outputs=[output_code, summary, status],
            )

    return demo


demo = build_app()


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        inbrowser=False,
    )

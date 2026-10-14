import os
from pathlib import Path
from typing import Any

import gradio as gr

from btl.annotate import annotate_source, collect_blocks, parse_python
from btl.java import collect_java_blocks, parse_java
from btl.model import ModelUnavailableError
from btl.remote import annotate_remote


REMOTE_URL = os.getenv("BTL_REMOTE_URL", "").strip()

try:
    import spaces
except ImportError:
    spaces = None


def zero_gpu_task(fn):
    if REMOTE_URL or spaces is None:
        return fn
    return spaces.GPU(duration=120)(fn)


CSS = (Path(__file__).parent / "assets" / "style.css").read_text(encoding="utf-8")


MODEL_LABELS = {
    "Base Mellum2": "base",
    "LoRA adapter (experimental)": "tuned",
    # Keep older CLI versions compatible with this API.
    "Base Mellum2 (richer)": "base",
    "Fine-tuned LoRA (concise)": "tuned",
}
MODEL_CHOICES = ["Base Mellum2", "LoRA adapter (experimental)"]


def cli_markup() -> str:
    return (
        '<section id="btl-cli" aria-label="Terminal command">'
        '<p class="btl-cli-label">Review staged files with a local model</p>'
        "<pre><code>git btl annotate --staged</code></pre>"
        '<a class="btl-cli-setup" href="https://huggingface.co/spaces/coolbeanz79/between-the-lines/blob/main/README.md" '
        'target="_blank" rel="noopener noreferrer">Local setup</a>'
        "</section>"
    )


def _annotate(
    source: str, model_label: str, language_label: str
) -> tuple[str, str, str]:
    model = MODEL_LABELS.get(model_label)
    if model is None:
        return "", source, "Select a supported comment model."
    source = source.removeprefix("\ufeff")
    language = language_label.lower()
    try:
        result = (
            annotate_remote(source, model, language, REMOTE_URL)
            if REMOTE_URL
            else annotate_source(source, model, language)
        )
    except ModelUnavailableError as exc:
        return "", source, f"Model unavailable: {exc}"
    except ImportError:
        return (
            "",
            source,
            "A required dependency is missing. Install the requirements for this deployment.",
        )
    return result.summary, result.annotated_source, result.status


@zero_gpu_task
def annotate_code(source: str, model_label: str) -> tuple[str, str, str]:
    """Retain the original Python endpoint for published CLI clients."""
    return _annotate(source, model_label, "Python")


@zero_gpu_task
def annotate_code_with_language(
    source: str, model_label: str, language_label: str
) -> tuple[str, str, str]:
    return _annotate(source, model_label, language_label)


def load_uploaded_source(file_obj: Any) -> tuple[str, str, str, str, str]:
    if file_obj is None:
        return (
            "",
            "",
            "",
            "Choose a `.py` or `.java` file to load it into the editor.",
            "Python",
        )

    path = Path(file_obj if isinstance(file_obj, (str, Path)) else file_obj.name)
    language = "Java" if path.suffix.lower() == ".java" else "Python"
    try:
        source = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return "", "", "", f"Could not read `{path.name}` as UTF-8 text.", language
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
        return (
            source,
            "",
            "",
            f"Loaded `{path.name}`, but parsing failed: {detail}",
            language,
        )
    except ImportError as exc:
        return (
            source,
            "",
            "",
            f"Loaded `{path.name}`, but Java support is unavailable: install the Tree-sitter dependencies ({str(exc)})",
            language,
        )

    return (
        source,
        "",
        "",
        f"Loaded `{path.name}`. Parsed {len(blocks)} declaration(s).",
        language,
    )


def update_language(language_label: str):
    is_java = language_label == "Java"
    return (
        gr.update(
            language=None if is_java else "python", label=f"Original {language_label}"
        ),
        gr.update(
            language=None if is_java else "python", label=f"Annotated {language_label}"
        ),
        gr.update(
            choices=[MODEL_CHOICES[0]] if is_java else MODEL_CHOICES,
            value=MODEL_CHOICES[0],
        ),
        cli_markup(),
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
                  <p>Add clear comments to Python and Java files. The app checks that Python's AST or Java's syntax tree stays unchanged after annotation.</p>
                </header>
                """
            )
            cli_command = gr.HTML(cli_markup())

            with gr.Row(equal_height=False, elem_id="btl-controls"):
                upload_file = gr.File(
                    label="Upload Python or Java File",
                    file_types=[".py", ".java"],
                    elem_classes=["btl-upload"],
                    scale=3,
                )
                with gr.Column(elem_id="btl-options", scale=2):
                    language_choice = gr.Dropdown(
                        label="Language",
                        choices=["Python", "Java"],
                        value="Python",
                        interactive=True,
                        elem_id="language_choice",
                    )
                    model_choice = gr.Dropdown(
                        label="Comment Model",
                        choices=MODEL_CHOICES,
                        value=MODEL_CHOICES[0],
                        interactive=True,
                        elem_id="model_choice",
                    )

            with gr.Row(equal_height=False, elem_id="btl-actions"):
                run_button = gr.Button("Annotate", variant="primary", scale=1)
                clear_button = gr.ClearButton(value="Clear", components=[], scale=1)
                # Textboxes preserve source whitespace and accept older CLI model labels.
                api_source = gr.Textbox(visible=False)
                api_output = gr.Textbox(visible=False)
                api_model = gr.Textbox(visible=False)
                multilang_api_button = gr.Button(visible=False)
                legacy_api_button = gr.Button(visible=False)

            with gr.Row(equal_height=True, elem_id="btl-editors"):
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

            with gr.Row(equal_height=True, elem_id="btl-results"):
                summary = gr.Textbox(
                    label="File Summary", lines=3, elem_id="summary_box"
                )
                status = gr.Textbox(label="Validation", lines=3, elem_id="status_box")

            clear_button.add([input_code, output_code, summary, status])

            run_button.click(
                annotate_code_with_language,
                inputs=[input_code, model_choice, language_choice],
                outputs=[summary, output_code, status],
                api_name=False,
            )
            multilang_api_button.click(
                annotate_code_with_language,
                inputs=[api_source, api_model, language_choice],
                outputs=[summary, api_output, status],
                api_name="annotate_multilang",
            )
            # Existing published CLI versions still call this two-argument API.
            legacy_api_button.click(
                annotate_code,
                inputs=[api_source, api_model],
                outputs=[summary, api_output, status],
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
                outputs=[
                    input_code,
                    output_code,
                    model_choice,
                    cli_command,
                    summary,
                    status,
                ],
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
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        inbrowser=False,
    )

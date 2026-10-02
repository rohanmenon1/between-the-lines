---
title: between-the-lines
sdk: gradio
sdk_version: 5.50.0
app_file: app.py
pinned: false
tags:
  - build-small-hackathon
  - backyard-ai
  - tiny-titan
  - well-tuned
  - code
---

# between-the-lines

A code-reading assistant for individual Python and Java files. A small code model proposes one explanatory comment above each standalone class or function/method declaration. The app inserts those comments itself, parses the result, and rejects output if the code's syntax tree changes.

Try the [live Space](https://huggingface.co/spaces/coolbeanz79/between-the-lines) or watch the [demo video](https://youtu.be/2cVZnw0t2g8). The original project was built for the [Build Small Hackathon](https://huggingface.co/spaces/build-small-hackathon/between-the-lines).

## How it works

1. Parse the input and find declarations that begin on their own line.
2. Ask Mellum2 for a file summary and a short comment for each declaration.
3. Normalize each model response to a single line comment, then insert it without asking the model to rewrite source code.
4. Parse the annotated file again and compare the trees while ignoring comments and source positions. On failure, return the original source.

Python uses the standard-library `ast` parser and compares the full AST, including docstrings. Java uses Tree-sitter Java and compares every non-comment syntax node and leaf token. The Java path rejects Unicode escapes in generated comments because Java processes them before tokenization. These checks preserve parsed code structure; they cannot establish that a generated explanation is accurate, and they do not replace project compilation or tests.

The **base Mellum2** model works for both languages. The **fine-tuned LoRA** option is Python-only because its training examples were Python. Java files use the base model.

The first Java version intentionally annotates classes, interfaces, enums, records, methods, and constructors. Java editor panes use plain text because the hosted Gradio editor does not provide Java highlighting in this configuration.

## CLI

The npm client sends the file to the hosted Space and writes a sibling file only when validation succeeds:

```bash
npx between-the-lines-cli path/to/file.py --model base --summary
npx between-the-lines-cli path/to/File.java --model base --summary
```

The output paths are `file.annotated.py` and `File.annotated.java`. Use `--output <path>` to choose a destination, `--in-place` to replace the input after validation, `--language python|java` for a file without a standard extension, or `--space <repo-id>` to target another deployment. The CLI defaults to `coolbeanz79/between-the-lines`.

The Java CLI requires a deployment with the `/annotate_multilang` endpoint. The older `/annotate` endpoint remains available for existing Python clients.

## Code map

| Path | Why it exists |
| --- | --- |
| `app.py` | Gradio UI, file upload, model and language selection, and API wiring. |
| `assets/style.css` | Presentation, separate from application behavior. |
| `btl/annotate.py` | Python block discovery, comment insertion, AST validation, and language routing. |
| `btl/java.py` | Java declaration discovery and syntax-tree validation. |
| `btl/model.py` | Model loading, cached inference, and output cleanup. |
| `btl/prompts.py` | Language-aware prompts, kept separate so prompts can change without altering validation. |
| `packages/npm/src/cli.js` | Terminal client that detects the language and writes only validated output. |
| `training/` | Hackathon dataset preparation and LoRA training scripts; not needed for hosted inference. |
| `tests/test_annotation.py` | Focused regression tests for the source-preservation boundary. |

Run the focused checks after installing the Python dependencies:

```bash
python -m unittest discover -s tests -v
node --check packages/npm/src/cli.js
```

The hosted Space uses a ZeroGPU allocation. Inference latency grows with the number of declarations because it generates a summary and comments sequentially; this is best suited to a small file, not an entire repository in one request.

## Hackathon context

The original Build Small submission used JetBrains Mellum2 12B-A2.5B Instruct. Its concise-comment LoRA adapter was trained on CodeSearchNet-derived Python examples using Modal. The project combines a Gradio web interface, a hosted npm CLI, and a validation boundary around model output.

Demo video: https://youtu.be/2cVZnw0t2g8

Hackathon post: https://x.com/bigbeanburt/status/2066668276462100883?s=20

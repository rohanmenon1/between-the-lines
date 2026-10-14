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

A code-reading assistant for Python and Java files. A local Git command and a hosted web app propose short comments for declarations that lack an adjacent comment or docstring. The tool inserts comments itself, parses the result, and rejects output if the code's syntax tree changes.

Try the [live Space](https://huggingface.co/spaces/coolbeanz79/between-the-lines) or watch the [demo video](https://youtu.be/2cVZnw0t2g8). The original project was built for the [Build Small Hackathon](https://huggingface.co/spaces/build-small-hackathon/between-the-lines).

## How it works

1. Parse the input and find declarations that begin on their own line.
2. Ask the selected model for short comments. The hosted app also asks Mellum2 for a file summary; the local command builds a summary from parsed declarations.
3. Normalize each model response to a single line comment, then insert it without asking the model to rewrite source code.
4. Parse the annotated file again and compare the trees while ignoring comments and source positions. On failure, return the original source.

Python uses the standard-library `ast` parser and compares the full AST, including docstrings. Java uses Tree-sitter Java and compares every non-comment syntax node and leaf token. The Java path rejects Unicode escapes in generated comments because Java processes them before tokenization. These checks preserve parsed code structure; they cannot establish that a generated explanation is accurate, and they do not replace project compilation or tests.

The hosted app uses **base Mellum2** for both languages. Its **fine-tuned LoRA** option is Python-only because its training examples were Python. The adapter is experimental: this repository does not include a paired quality benchmark showing that it performs better than the base model. The Git command uses a smaller model through local Ollama instead.

The first Java version intentionally annotates classes, interfaces, enums, records, methods, and constructors. Java editor panes use plain text because the hosted Gradio editor does not provide Java highlighting in this configuration.

## Local Git workflow

Install [Ollama](https://ollama.com/download), download a small model, and install this repository in an active Python environment:

```bash
ollama pull qwen3:4b-instruct
python -m pip install -e .
```

With that environment active, Git finds the installed `git-btl` command as `git btl`. Stage the Python or Java files you want to review, then run:

```bash
git btl annotate --staged --dry-run
git btl annotate --staged
git diff
git add path/to/file.py
git commit
```

The preview prints the proposed diff. The second command writes comments to the working files and leaves Git's index untouched, so you decide which comments to stage. Run it before committing; a pre-push hook runs after the commit has already been made. The command refuses a file with unstaged edits or an unresolved conflict. It accepts UTF-8 `.py` and `.java` files and skips declarations that already have a nearby comment or Python docstring. Use `--model <ollama-model>` to try another installed local model.

The default Qwen 4B model has a 2.5 GB download. On an RTX 3050 with 6 GB VRAM, Ollama reported 3.2 GB loaded, 100% on the GPU, at a 4,096-token context during a small local test. Memory use and speed will vary with context length and other GPU activity. Source is sent only to Ollama on `127.0.0.1` in this workflow.

This path has no paid inference API or subscription requirement if you already have suitable hardware. That is a narrower claim than “cheaper than Copilot”: [Copilot Free and Copilot Student](https://docs.github.com/en/copilot/get-started/plans) cost $0, while [Copilot Pro](https://github.com/features/copilot/plans) is $10/month as of October 2026. A cost comparison also needs to account for electricity, hardware, time, and the fact that Copilot offers different features.

## Hosted CLI

The older npm client sends the file to the hosted Space and writes a sibling file only when validation succeeds:

```bash
npx between-the-lines-cli path/to/file.py --model base --summary
npx between-the-lines-cli path/to/File.java --model base --summary
```

The output paths are `file.annotated.py` and `File.annotated.java`. Use `--output <path>` to choose a destination, `--in-place` to replace the input after validation, `--language python|java` for a file without a standard extension, or `--space <repo-id>` to target another deployment. The CLI defaults to `coolbeanz79/between-the-lines`.

The Java CLI requires a deployment with the `/annotate_multilang` endpoint. The older `/annotate` endpoint remains available for existing Python clients.

## Replit demo

The Replit app runs the same UI and sends annotation requests to the Hugging Face Space. It does not load Mellum2 or need a GPU. Files submitted through this demo leave the Replit app for inference; the local Git command still uses Ollama on your own machine.

Before publishing Replit, update the Hugging Face Space from this repository. The older Python-only backend does not expose `/annotate_multilang`, which this demo requires for both languages.

1. Import this repository with [Replit's GitHub importer](https://replit.com/import).
2. Select **Run**. The `.replit` configuration installs `requirements-replit.txt`, sets `BTL_REMOTE_URL` to the public Space, and starts the UI on port 8080.
3. Try a small Python file and a Java file. Check that comments appear and validation succeeds.
4. Publish the app with an Autoscale deployment. Build and run commands are included in `.replit`. Share the published app URL, and include the Replit project link if the application asks for it.

Use `requirements-replit.txt` for this deployment. The root `requirements.txt` installs the model runtime for Hugging Face or local Mellum2 inference. Replit's automatic import guessing is disabled so it does not install that runtime from optional imports.

The demo depends on the Space being available and can wait for its GPU allocation. With `BTL_REMOTE_URL` unset, `python app.py` uses the local Mellum2 runtime described below.

## Running the hosted UI locally

Install the Python dependencies with `pip install -r requirements.txt`, then run `python app.py`. Parsing and the UI work without a GPU. For practical local comment generation, use a CUDA-enabled PyTorch installation and a GPU with enough memory for Mellum2. CPU inference has not been tested with this app.

The [base model files](https://huggingface.co/JetBrains/Mellum2-12B-A2.5B-Instruct/tree/main) are about 24 GB. This app loads its 12 billion parameters in 4-bit NF4, with BF16 computation. Quantized weights alone need roughly 6 GB; the runtime also needs memory for other layers, quantization data, and generated tokens. Budget around 16 GB of GPU memory for local inference. That is a planning estimate, not a measured minimum. The LoRA adapter is about 32 MB, but it still loads the full base model.

On a [ZeroGPU Space](https://huggingface.co/docs/hub/spaces-zerogpu), the app requests a GPU for up to 120 seconds per annotation call. Local inference does not use that allocation.

## Code map

| Path | Why it exists |
| --- | --- |
| `app.py` | Gradio UI, file upload, model and language selection, and API wiring. |
| `assets/style.css` | Presentation, separate from application behavior. |
| `.replit` and `requirements-replit.txt` | Replit run, publishing, and port configuration with only the UI and parser dependencies. |
| `btl/annotate.py` | Python declaration discovery, AST validation, and language routing. |
| `btl/core.py` | Shared result types, comment insertion, and declaration summaries used by both languages. |
| `btl/java.py` | Java declaration discovery and syntax-tree validation. |
| `btl/model.py` | Hosted Mellum2 loading, local Ollama requests, and output cleanup. |
| `btl/remote.py` | Calls the hosted annotation API and checks returned comments and syntax before displaying the result. |
| `btl/git_cli.py` | Finds staged files, rejects partial staging, and prints changes for review. |
| `btl/prompts.py` | Language-aware prompts, kept separate so prompts can change without altering validation. |
| `packages/npm/src/cli.js` | Terminal client that detects the language and writes only validated output. |
| `training/` | Hackathon dataset preparation and LoRA training scripts; not needed for hosted inference. |
| `tests/` | Regression tests for source preservation and the staged-file workflow. |

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

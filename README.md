---
title: between-the-lines
emoji: 🟩
colorFrom: green
colorTo: gray
sdk: gradio
sdk_version: 5.50.0
app_file: app.py
pinned: false
tags:
  - build-small-hackathon
  - backyard-ai
  - tiny-titan
  - well-tuned
  - llama-cpp
  - code
---

# between-the-lines

A small-model code-reading assistant for Python files. It parses a single file deterministically, asks Mellum2 to add explanatory comments, and validates that the annotated output still parses to the same executable AST shape.

The app includes two modes:

- **Base Mellum2:** richer comments from the base instruct model through llama.cpp.
- **Fine-tuned LoRA:** a concise comment style trained on CodeSearchNet-derived Python examples with Modal.

The model never edits code directly. It only proposes comments, and the app rejects any annotated file whose semantic AST changes.

## Hackathon Fit

- **Track:** Backyard AI
- **Interface:** Gradio Space
- **Model:** JetBrains Mellum2 12B-A2.5B Instruct, with a LoRA adapter for concise Python comments.
- **Training:** LoRA SFT on Modal using 10,000 train rows and 500 eval rows.
- **Correctness story:** the model proposes comments only; Python AST parsing and validation guard against semantic edits.
- **Deployment:** local/Space-hosted model inference, no external model API.

Demo video and social post links will be added before submission.

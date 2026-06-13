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
  - off-brand
---

# between-the-lines

A small-model code-reading assistant for Python files. It parses a single file deterministically, asks a compact model to add explanatory comments/docstrings, and validates that the annotated output still parses to the same executable AST shape.

## Hackathon Fit

- **Track:** Backyard AI
- **Interface:** Gradio Space
- **Model constraint:** all models will stay under the hackathon's 32B parameter limit; target model is under 4B for Tiny Titan eligibility.
- **Correctness story:** the model proposes comments only; Python AST parsing and validation guard against semantic edits.

Demo video and social post links will be added before submission.

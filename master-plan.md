# between-the-lines — Project Plan

**Hackathon:** Build Small (Hugging Face / build-small-hackathon)
**Track:** Backyard AI
**Deadline:** June 15, 2026
**Space:** build-small-hackathon/between-the-lines

---

## 1. The Idea

A tool that takes a single Python file and:
1. Parses it deterministically via `ast` (no LLM involved in parsing)
2. Uses a small fine-tuned LLM to generate **inline comments/docstrings** for each function/class/method
3. Uses the LLM to generate a **top-of-file summary/overview**
4. **Validates** every LLM-generated comment by re-parsing the modified file and confirming the AST (minus new comment/docstring nodes) is identical to the original — reject any edit that changes semantics
5. Displays original vs. annotated code side-by-side in a Gradio app

**Why this is a good fit:**
- Real value: helps someone read/understand unfamiliar or undercommented code (e.g. a parent of a CS student, a non-technical co-founder, a junior dev onboarding)
- Technically deep: SFT on code→comment pairs, AST-based deterministic validation, narrow well-defined task suited to small models
- Honest fit with "small model" constraint: comment generation per-block is a narrow task, doesn't need a huge model
- Differentiator: most entries are "model + Gradio wrapper"; this has a real correctness/validation story

**Open design questions (resolve before dataset work):**
- [ ] Comment style: "what it does" (safer, grounded) vs "why" (more valuable, higher hallucination risk) — leaning toward "what, with light why where obvious"
- [ ] Target audience / tone: beginner reading someone else's code vs developer onboarding vs non-technical stakeholder — possibly a user-facing toggle
- [ ] Scope: single-file only for hackathon (no cross-file/import resolution); optional "context files" as stretch goal

---

## 2. Architecture

```
Input: single .py file
   |
   v
[Python] AST walk -> extract each function/class/method
   (source text, signature, call-site context within file)
   |
   v
[LLM] per-block call -> generate inline comment/docstring
   |
   v
[LLM] separate call -> generate top-of-file summary using per-block outputs
   |
   v
[Python] insert comments/docstrings at AST-derived positions
   |
   v
[Python] re-parse modified file, diff AST against original
   (excluding new comment/docstring nodes) -> reject failing blocks
   |
   v
[Gradio] display original vs annotated side-by-side + top-of-file summary
```

**Split of responsibilities:**
- Python (deterministic): parsing, insertion, validation, diffing — guarantees semantics preserved
- LLM (fine-tuned, small): generates comment/docstring text only, never rewrites code

---

## 3. Model & Training Plan

- **Base model:** small (1-4B params) — targets "Tiny Titan" badge ($1,000)
  - Candidates: Qwen small variants, MiniCPM, Cohere Tiny Aya — TBD
- **Method:** LoRA SFT on (code block, good comment/docstring) pairs
  - Stretch: DPO pass if time allows after solid SFT result
- **Dataset:** mined from well-commented open-source repos / synthetic generation to fill gaps
  - Format: (function source + minimal call-site context) -> (docstring/comment)
- **Training compute:** Modal ($250 credit) — NOT Zero GPU (Zero GPU is for inference only, has time limits unsuitable for training)
- **Deployment:** Zero GPU on the Space for inference (`@spaces.GPU` decorated function), within 10-app-per-user limit

---

## 4. Badge / Prize Targeting

| Badge/Prize | Plan to qualify |
|---|---|
| Tiny Titan ($1,000) | Use 1-4B base model |
| Well-Tuned | Publish fine-tuned model on HF |
| Off the Grid | No cloud API calls at inference time (fully local/Space-hosted) |
| Llama Champion | Run inference via llama.cpp if feasible |
| Field Notes | Write up idea + tech in README frontmatter |
| Backyard AI placement (3rd/4th) | Polish, correctness story, real-person framing |
| Bonus Quest Champion ($2,000) | Stack 4-5+ badges above |

---

## 5. Timeline

- **Thu night (tonight) + Fri 12th — Prep**
  - [x] Create Space (between-the-lines, Gradio, ZeroGPU, Public)
  - [ ] Push placeholder app, confirm build works
  - [ ] Resolve open design questions (comment style, audience, scope)
  - [ ] Pick base model
  - [ ] Build/curate SFT dataset (mined + synthetic)
  - [ ] Set up Modal, run end-to-end sanity training job on tiny subset

- **Sat 13th — Core build**
  - [ ] Real LoRA SFT training run(s) on Modal (2-3 iterations)
  - [ ] Build AST parsing/validation module (pure Python, can proceed in parallel)
  - [ ] Build Gradio app skeleton wired to base model first
  - [ ] Evaluate training runs, iterate

- **Sun 14th — Integration & polish**
  - [ ] Swap in best fine-tuned checkpoint
  - [ ] Full pipeline test (parse -> generate -> validate -> display)
  - [ ] Optional DPO pass if SFT solid
  - [ ] llama.cpp conversion/quantization (Off the Grid / Llama Champion)
  - [ ] Before/after eval examples for demo

- **Mon 15th — Submission day**
  - [ ] Final bug fixes, buffer
  - [ ] Record demo video
  - [ ] Publish model + dataset to HF
  - [ ] Write README (frontmatter tags, write-up)
  - [ ] Social media post
  - [ ] Submit early

---

## 6. Status Log

- **June 11, ~10pm:** Idea locked. Space created (between-the-lines, build-small-hackathon org, Gradio, ZeroGPU, Public). Placeholder app drafted, ready to push.

- **June 13, late evening:** Gradio app now has upload support, empty initial editor, Mellum2 GGUF base-model inference path, and AST validation after inserting model comments. Visible AST outline/debug UI and sample examples were removed per design direction. Current model wrapper lives in `btl/model.py`; prompt lives in `btl/prompts.py`; app orchestration remains in root `app.py`.

- **June 13, late evening:** Data/training scaffold added. `data/` contains raw/processed/eval placeholders and README. `training/build_dataset.py` converts CodeSearchNet Python examples into JSONL SFT records: code block without leading docstring -> one `# ...` comment. Generated data files are gitignored.

- **June 13, late evening blockers:** Local Windows Python can install packages only outside sandbox due temp permission issues. `llama-cpp-python` and `datasets` were installed successfully after escalation. CodeSearchNet download was blocked locally by Python SSL certificate verification against Hugging Face, even when run unsandboxed and with `certifi`/HF SSL env attempts.

- **June 14, morning:** Modal CLI installed and authenticated token already exists in config. Modal initially failed with `CERTIFICATE_VERIFY_FAILED` because Norton Web/Mail Shield is intercepting TLS and Python's certifi store does not trust that chain. Installed `truststore` and added `training/modal_truststore.py` wrapper. Modal smoke test now succeeds with:
  - `$env:PYTHONUTF8='1'; py training\modal_truststore.py run training\modal_smoke.py`

- **June 14, morning:** Modal data collection is set up and working. Added `training/modal_build_dataset.py`, mounted persistent Modal Volume `between-the-lines-data`, and generated filtered CodeSearchNet Python comment data. A 10,000 train / 500 eval build completed successfully in Modal:
  - train: `/data/processed/codesearchnet_python_comments.jsonl`
  - eval: `/data/eval/codesearchnet_python_comments_eval.jsonl`

- **June 14, morning:** Rebuilt the CodeSearchNet dataset after the first cleanup filter pass. Current filters reject docstrings with semicolons, underscores, signature/call-like text, and comments starting with `This `, plus earlier obvious-noise filters. A 20-row Modal sample review looked mostly usable for a LoRA sanity run, but still has some generic comments and source-docstring typos, so final training should include another curation pass if time allows.

- **June 14, evening:** Completed Mellum2 LoRA SFT on Modal using 10,000 train rows and 500 eval rows. Training resumed from a committed checkpoint after local DNS/heartbeat interruptions and finished all 625 optimizer steps. Final adapter artifacts are in Modal Volume `between-the-lines-models` at `/models/mellum2-comment-lora/final`, with checkpoints at `checkpoint-250`, `checkpoint-500`, and `checkpoint-625`. Observed eval losses during the run: about `1.653` at step 300 and `1.639` at step 400; final eval completed but the CLI log tail did not include the returned metrics object.

- **June 14, evening:** Ran base-vs-LoRA qualitative comparison on 6 held-out eval examples. The tuned adapter reliably produced concise `# ...` comments that matched the dataset style, but it often became more generic than the base model. The base model sometimes gave richer explanatory comments. Product decision still needed: use LoRA for predictable short comments, base for richer explanations, or tune another pass with better labels.

---

## 7. Resume Checklist

### App / Product

- [x] File upload loads `.py` files into the editor
- [x] App starts with empty editor, no example widget
- [x] Base model path added via `llama-cpp-python`
- [x] Default model currently: `JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q8_0`
- [x] Prompt requires exactly one Python comment starting with `# `
- [x] Insert generated comments above AST-detected classes/functions
- [x] Re-parse and compare semantic AST after insertion
- [ ] Decide whether default Space inference should use Q8 or smaller Q4_K_M
- [ ] Add file/block size guardrails so huge files do not hang demo
- [ ] Run actual Mellum inference end-to-end once weights download works
- [ ] Polish README with architecture, model choice, validation story, badges, Modal use, demo/social placeholders

### Data

- [x] Created `data/` scaffold
- [x] Created `training/build_dataset.py`
- [x] Added `datasets` dependency
- [x] Run CodeSearchNet builder on Modal/Linux:
  - `$env:PYTHONUTF8='1'; py training\modal_truststore.py run training\modal_build_dataset.py --limit 10000 --eval-size 500`
- [x] Inspect 20 generated rows manually for quality after first cleanup pass
- [x] Tune first cleanup filters for obvious noisy comments
- [ ] Inspect 25-50 more generated rows before final/full training run
- [ ] Tune filters again if comments are still too noisy or generic
- [ ] Publish final dataset or a clean sample dataset to Hugging Face

### Modal

- [x] Install Modal CLI locally:
  - `py -m pip install modal`
- [x] Authenticate:
  - `py -m modal setup`
- [x] Fix Windows/Norton TLS issue using `truststore` wrapper
- [x] Smoke test Modal:
  - `$env:PYTHONUTF8='1'; py training\modal_truststore.py run training\modal_smoke.py`
- [x] Add Modal script for dataset build + LoRA training
- [x] Add Modal script for dataset build: `training/modal_build_dataset.py`
- [x] Create/use Modal volume for datasets: `between-the-lines-data`
- [x] Add Modal script for LoRA training
- [x] Add Modal script for LoRA training: `training/modal_train_lora.py`
- [x] Create/cache Modal volume for model artifacts
- [x] Create/use Modal volume for model artifacts: `between-the-lines-models`
- [x] Run small sanity job first
- [x] Run Mellum LoRA target inspection; confirmed target modules: `q_proj`, `k_proj`, `v_proj`, `o_proj`
- [x] Run real LoRA SFT job
- [ ] Upload adapter/model artifacts to Hugging Face

### Training Plan

- [x] Use Mellum2 Instruct as base for current product direction
- [x] First LoRA sanity run: 16 examples to validate Trainer/PEFT/Modal path
- [x] First real LoRA run: 10,000 train examples / 500 eval examples
- [x] Compare base vs tuned outputs on held-out eval examples
- [ ] Integrate tuned model only if it improves output quality/control

### Known Environment Notes

- Use `py`, not `python`, on this Windows machine. `python.exe` resolves to a Microsoft Store shim and previously failed.
- `py -m py_compile app.py btl\model.py btl\prompts.py training\build_dataset.py` passes.
- `llama-cpp-python` installed locally as `0.3.29`.
- `datasets` installed locally as `5.0.0`.
- `modal` installed locally as `1.5.0`.
- `truststore` installed locally as `0.10.4`.
- Use the Modal wrapper on this Windows machine:
  - `$env:PYTHONUTF8='1'; py training\modal_truststore.py run <modal_script.py>`
- Local Hugging Face dataset downloads may fail unless run through code that injects `truststore`, because Norton Web/Mail Shield is presenting the TLS certificate chain.

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

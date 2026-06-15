# Training

Scripts for creating the SFT dataset and training/evaluating a small comment-generation model.

Current flow:

1. Build JSONL data from CodeSearchNet Python examples.
2. Fine-tune the base model with LoRA on Modal.
3. Compare base vs. fine-tuned outputs on held-out examples.

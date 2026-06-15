from pathlib import Path

import modal


app = modal.App("between-the-lines-train")

data_volume = modal.Volume.from_name("between-the-lines-data")
model_volume = modal.Volume.from_name("between-the-lines-models", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .pip_install(
        "accelerate==1.12.0",
        "bitsandbytes==0.49.0",
        "datasets",
        "huggingface_hub",
        "peft==0.18.0",
        "safetensors",
        "torch",
        "git+https://github.com/huggingface/transformers.git",
    )
)

BASE_MODEL = "JetBrains/Mellum2-12B-A2.5B-Instruct"
TRAIN_PATH = "/data/processed/codesearchnet_python_comments.jsonl"
EVAL_PATH = "/data/eval/codesearchnet_python_comments_eval.jsonl"
OUTPUT_DIR = "/models/mellum2-comment-lora"
FINAL_DIR = f"{OUTPUT_DIR}/final"


def _format_text(prompt: str, completion: str, eos_token: str) -> tuple[str, str]:
    prompt = prompt.rstrip() + "\n\n"
    completion = completion.strip() + eos_token
    return prompt, prompt + completion


def _latest_checkpoint(output_dir: str) -> str | None:
    checkpoints = []
    for path in Path(output_dir).glob("checkpoint-*"):
        try:
            step = int(path.name.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            continue
        checkpoints.append((step, path))

    if not checkpoints:
        return None

    return str(max(checkpoints, key=lambda item: item[0])[1])


@app.function(
    image=image,
    gpu="A100-80GB",
    volumes={"/data": data_volume, "/models": model_volume},
    timeout=60 * 30,
)
def inspect_mellum_lora_targets() -> dict[str, object]:
    import torch
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        trust_remote_code=True,
        device_map="auto",
        quantization_config=quantization_config,
    )

    suffix_counts: dict[str, int] = {}
    linear_like = []
    for name, module in model.named_modules():
        class_name = module.__class__.__name__.lower()
        if "linear" not in class_name:
            continue
        suffix = name.split(".")[-1]
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        if len(linear_like) < 80:
            linear_like.append(name)

    return {
        "base_model": BASE_MODEL,
        "linear_suffix_counts": suffix_counts,
        "sample_linear_modules": linear_like,
    }


@app.function(
    image=image,
    gpu="A100-80GB",
    volumes={"/data": data_volume, "/models": model_volume},
    timeout=60 * 60 * 6,
)
def train_lora(
    max_train_examples: int = 10_000,
    max_eval_examples: int = 500,
    max_length: int = 1024,
    epochs: float = 1.0,
    learning_rate: float = 2e-4,
    lora_rank: int = 16,
    eval_steps: int = 250,
    save_steps: int = 100,
    resume_from_latest: bool = False,
) -> dict[str, object]:
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
        TrainerCallback,
    )

    dataset = load_dataset(
        "json",
        data_files={"train": TRAIN_PATH, "eval": EVAL_PATH},
    )
    if max_train_examples:
        dataset["train"] = dataset["train"].select(range(min(max_train_examples, len(dataset["train"]))))
    if max_eval_examples:
        dataset["eval"] = dataset["eval"].select(range(min(max_eval_examples, len(dataset["eval"]))))

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def tokenize_row(row):
        prompt, full_text = _format_text(row["prompt"], row["completion"], tokenizer.eos_token)
        prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        encoded = tokenizer(
            full_text,
            add_special_tokens=False,
            truncation=True,
            max_length=max_length,
        )
        labels = list(encoded["input_ids"])
        prompt_len = min(len(prompt_ids), len(labels))
        labels[:prompt_len] = [-100] * prompt_len
        encoded["labels"] = labels
        return encoded

    tokenized = dataset.map(
        tokenize_row,
        remove_columns=dataset["train"].column_names,
        desc="Tokenizing prompt/comment pairs",
    )

    class CompletionOnlyCollator:
        def __init__(self, tokenizer, label_pad_token_id: int = -100):
            self.tokenizer = tokenizer
            self.label_pad_token_id = label_pad_token_id

        def __call__(self, features):
            max_len = max(len(feature["input_ids"]) for feature in features)
            batch = {"input_ids": [], "attention_mask": [], "labels": []}
            for feature in features:
                pad_len = max_len - len(feature["input_ids"])
                batch["input_ids"].append(feature["input_ids"] + [self.tokenizer.pad_token_id] * pad_len)
                batch["attention_mask"].append(feature["attention_mask"] + [0] * pad_len)
                batch["labels"].append(feature["labels"] + [self.label_pad_token_id] * pad_len)
            return {key: torch.tensor(value, dtype=torch.long) for key, value in batch.items()}

    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        trust_remote_code=True,
        device_map="auto",
        quantization_config=quantization_config,
    )
    model = prepare_model_for_kbit_training(model)

    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]
    lora_config = LoraConfig(
        r=lora_rank,
        lora_alpha=lora_rank * 2,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=target_modules,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    class CommitOnSaveCallback(TrainerCallback):
        def on_save(self, args, state, control, **kwargs):
            model_volume.commit()
            return control

    args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=epochs,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=16,
        learning_rate=learning_rate,
        warmup_ratio=0.03,
        logging_steps=10,
        eval_strategy="steps",
        eval_steps=eval_steps,
        save_strategy="steps",
        save_steps=save_steps,
        save_total_limit=3,
        bf16=True,
        gradient_checkpointing=True,
        optim="paged_adamw_8bit",
        report_to="none",
        remove_unused_columns=False,
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["eval"],
        data_collator=CompletionOnlyCollator(tokenizer),
        callbacks=[CommitOnSaveCallback()],
    )
    resume_checkpoint = _latest_checkpoint(OUTPUT_DIR) if resume_from_latest else None
    train_result = trainer.train(resume_from_checkpoint=resume_checkpoint)
    eval_result = trainer.evaluate()

    Path(FINAL_DIR).mkdir(parents=True, exist_ok=True)
    trainer.save_model(FINAL_DIR)
    tokenizer.save_pretrained(FINAL_DIR)
    model_volume.commit()

    return {
        "base_model": BASE_MODEL,
        "adapter_path": FINAL_DIR,
        "train_rows": len(tokenized["train"]),
        "eval_rows": len(tokenized["eval"]),
        "resume_checkpoint": resume_checkpoint,
        "train_metrics": train_result.metrics,
        "eval_metrics": eval_result,
    }


@app.function(
    image=image,
    gpu="A100-80GB",
    volumes={"/data": data_volume, "/models": model_volume},
    timeout=60 * 60,
)
def compare_base_and_lora(
    count: int = 8,
    seed: int = 7,
    max_length: int = 1024,
    max_new_tokens: int = 48,
) -> list[dict[str, str | int]]:
    import gc
    import json
    import random

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    with Path(EVAL_PATH).open("r", encoding="utf-8") as file:
        rows = [json.loads(line) for line in file if line.strip()]

    rng = random.Random(seed)
    samples = rng.sample(rows, min(count, len(rows)))

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )

    def load_base_model():
        model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL,
            trust_remote_code=True,
            device_map="auto",
            quantization_config=quantization_config,
        )
        model.eval()
        return model

    def generate(model, prompt: str) -> str:
        encoded = tokenizer(
            prompt.rstrip() + "\n\n",
            return_tensors="pt",
            truncation=True,
            max_length=max_length,
        ).to(model.device)
        with torch.inference_mode():
            output_ids = model.generate(
                **encoded,
                do_sample=False,
                max_new_tokens=max_new_tokens,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )[0]
        generated_ids = output_ids[encoded["input_ids"].shape[-1] :]
        text = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
        return text.splitlines()[0].strip() if text else ""

    base_model = load_base_model()
    base_outputs = [generate(base_model, row["prompt"]) for row in samples]
    del base_model
    gc.collect()
    torch.cuda.empty_cache()

    tuned_model = PeftModel.from_pretrained(load_base_model(), FINAL_DIR)
    tuned_model.eval()
    tuned_outputs = [generate(tuned_model, row["prompt"]) for row in samples]

    results = []
    for row, base_output, tuned_output in zip(samples, base_outputs, tuned_outputs):
        code = row["code"]
        results.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "name": row["name"],
                "line_count": row["metadata"]["line_count"],
                "gold": row["completion"],
                "base": base_output,
                "tuned": tuned_output,
                "code": code[:900] + ("..." if len(code) > 900 else ""),
            }
        )
    return results


@app.function(
    image=image,
    gpu="A100-80GB",
    volumes={"/data": data_volume, "/models": model_volume},
    timeout=60 * 60,
)
def compare_external_python_block(
    url: str,
    block_name: str = "",
    max_source_lines: int = 80,
    max_length: int = 1024,
    max_new_tokens: int = 48,
) -> dict[str, object]:
    import ast
    import gc
    import json
    import urllib.request

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    with urllib.request.urlopen(url, timeout=60) as response:
        source = response.read().decode("utf-8")

    tree = ast.parse(source)
    candidates = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.end_lineno is None:
            continue
        block_source = ast.get_source_segment(source, node)
        if not block_source:
            continue
        line_count = len(block_source.splitlines())
        if line_count > max_source_lines:
            continue
        kind = "class" if isinstance(node, ast.ClassDef) else "async function" if isinstance(node, ast.AsyncFunctionDef) else "function"
        candidates.append((kind, node.name, line_count, block_source))

    if not candidates:
        raise ValueError(f"No top-level function/class with <= {max_source_lines} lines found in {url}")

    if block_name:
        matches = [candidate for candidate in candidates if candidate[1] == block_name]
        if not matches:
            names = ", ".join(candidate[1] for candidate in candidates[:20])
            raise ValueError(f"Block {block_name!r} not found. Available candidates include: {names}")
        kind, name, line_count, block_source = matches[0]
    else:
        kind, name, line_count, block_source = candidates[0]

    def dataset_contains(path: str, code: str) -> bool:
        with Path(path).open("r", encoding="utf-8") as file:
            return any(json.loads(line).get("code") == code for line in file if line.strip())

    prompt = (
        'Write exactly one concise Python comment starting with "# " for this code block.\n\n'
        "```python\n"
        f"{block_source.strip()}\n"
        "```\n"
    )

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )

    def load_base_model():
        model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL,
            trust_remote_code=True,
            device_map="auto",
            quantization_config=quantization_config,
        )
        model.eval()
        return model

    def generate(model) -> str:
        encoded = tokenizer(
            prompt.rstrip() + "\n\n",
            return_tensors="pt",
            truncation=True,
            max_length=max_length,
        ).to(model.device)
        with torch.inference_mode():
            output_ids = model.generate(
                **encoded,
                do_sample=False,
                max_new_tokens=max_new_tokens,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )[0]
        generated_ids = output_ids[encoded["input_ids"].shape[-1] :]
        text = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
        return text.splitlines()[0].strip() if text else ""

    base_model = load_base_model()
    base_output = generate(base_model)
    del base_model
    gc.collect()
    torch.cuda.empty_cache()

    tuned_model = PeftModel.from_pretrained(load_base_model(), FINAL_DIR)
    tuned_model.eval()
    tuned_output = generate(tuned_model)

    return {
        "url": url,
        "kind": kind,
        "name": name,
        "line_count": line_count,
        "in_train_exact": dataset_contains(TRAIN_PATH, block_source.strip()),
        "in_eval_exact": dataset_contains(EVAL_PATH, block_source.strip()),
        "base": base_output,
        "tuned": tuned_output,
        "code_preview": "\n".join(block_source.splitlines()[:20]),
    }


@app.local_entrypoint()
def main(
    mode: str = "inspect",
    max_train_examples: int = 10_000,
    max_eval_examples: int = 500,
    max_length: int = 1024,
    epochs: float = 1.0,
    learning_rate: float = 2e-4,
    lora_rank: int = 16,
    eval_steps: int = 250,
    save_steps: int = 100,
    resume_from_latest: bool = False,
    compare_count: int = 8,
    compare_seed: int = 7,
    max_new_tokens: int = 48,
    external_url: str = "",
    external_name: str = "",
) -> None:
    if mode == "inspect":
        print(inspect_mellum_lora_targets.remote())
        return
    if mode in {"train", "spawn-train"}:
        kwargs = dict(
            max_train_examples=max_train_examples,
            max_eval_examples=max_eval_examples,
            max_length=max_length,
            epochs=epochs,
            learning_rate=learning_rate,
            lora_rank=lora_rank,
            eval_steps=eval_steps,
            save_steps=save_steps,
            resume_from_latest=resume_from_latest,
        )
        if mode == "spawn-train":
            call = train_lora.spawn(**kwargs)
            print(f"Spawned remote training call: {call.object_id}")
            return

        print(train_lora.remote(**kwargs))
        return
    if mode == "compare":
        rows = compare_base_and_lora.remote(
            count=compare_count,
            seed=compare_seed,
            max_length=max_length,
            max_new_tokens=max_new_tokens,
        )
        for index, row in enumerate(rows, start=1):
            print(f"\n--- SAMPLE {index}: {row['kind']} {row['name']} ({row['line_count']} lines) ---")
            print(f"GOLD:  {row['gold']}")
            print(f"BASE:  {row['base']}")
            print(f"TUNED: {row['tuned']}")
            print(row["code"])
        return
    if mode == "compare-external":
        if not external_url:
            raise ValueError("--external-url is required for compare-external")
        row = compare_external_python_block.remote(
            url=external_url,
            block_name=external_name,
            max_length=max_length,
            max_new_tokens=max_new_tokens,
        )
        print(f"URL: {row['url']}")
        print(f"BLOCK: {row['kind']} {row['name']} ({row['line_count']} lines)")
        print(f"EXACT TRAIN MATCH: {row['in_train_exact']}")
        print(f"EXACT EVAL MATCH: {row['in_eval_exact']}")
        print(f"BASE:  {row['base']}")
        print(f"TUNED: {row['tuned']}")
        print("\nCODE PREVIEW:")
        print(row["code_preview"])
        return
    raise ValueError(f"Unknown mode: {mode}")

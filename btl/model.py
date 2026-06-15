import os
from functools import lru_cache
from typing import Literal

from .prompts import build_comment_messages


DEFAULT_MODEL_REPO = "JetBrains/Mellum2-12B-A2.5B-Instruct-GGUF-Q8_0"
DEFAULT_MODEL_FILE = "Mellum2-12B-A2.5B-Instruct-Q8_0.gguf"
DEFAULT_BASE_TRANSFORMERS_MODEL = "JetBrains/Mellum2-12B-A2.5B-Instruct"
DEFAULT_TUNED_ADAPTER_REPO = "rohanmenon1/between-the-lines-mellum2-lora"

ModelVariant = Literal["base", "tuned"]


class ModelUnavailableError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load_base_llm():
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise ModelUnavailableError(
            "llama-cpp-python is not installed. Install requirements before using model annotations."
        ) from exc

    repo_id = os.getenv("BTL_MODEL_REPO", DEFAULT_MODEL_REPO)
    filename = os.getenv("BTL_MODEL_FILE", DEFAULT_MODEL_FILE)
    n_ctx = int(os.getenv("BTL_MODEL_CTX", "4096"))
    n_gpu_layers = int(os.getenv("BTL_MODEL_GPU_LAYERS", "-1"))

    try:
        return Llama.from_pretrained(
            repo_id=repo_id,
            filename=filename,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
    except Exception as exc:
        raise ModelUnavailableError(
            f"Could not load `{repo_id}` / `{filename}` with llama-cpp-python: {exc}"
        ) from exc


def _clean_comment(text: str) -> str:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    line = line.strip("`").strip()

    if line.startswith("Comment:"):
        line = line.removeprefix("Comment:").strip()

    if not line.startswith("#"):
        line = "# " + line.lstrip("# ").strip()

    if not line.startswith("# "):
        line = "# " + line[1:].strip()

    return line[:240].rstrip()


@lru_cache(maxsize=1)
def _load_tuned_model():
    adapter_path_or_repo = (
        os.getenv("BTL_TUNED_ADAPTER_PATH")
        or os.getenv("BTL_TUNED_ADAPTER_REPO")
        or DEFAULT_TUNED_ADAPTER_REPO
    )
    if not adapter_path_or_repo:
        raise ModelUnavailableError(
            "Tuned LoRA adapter is not configured. Set BTL_TUNED_ADAPTER_PATH or BTL_TUNED_ADAPTER_REPO."
        )

    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        raise ModelUnavailableError(
            "Tuned LoRA inference requires torch, transformers, peft, and bitsandbytes."
        ) from exc

    model_name = os.getenv("BTL_TUNED_BASE_MODEL", DEFAULT_BASE_TRANSFORMERS_MODEL)
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )
        base_model = AutoModelForCausalLM.from_pretrained(
            model_name,
            trust_remote_code=True,
            device_map="auto",
            quantization_config=quantization_config,
        )
        model = PeftModel.from_pretrained(base_model, adapter_path_or_repo)
        model.eval()
        return tokenizer, model
    except Exception as exc:
        raise ModelUnavailableError(f"Could not load tuned LoRA adapter `{adapter_path_or_repo}`: {exc}") from exc


def generate_comment(kind: str, name: str, source: str, variant: ModelVariant = "base") -> str:
    if variant == "tuned":
        return generate_comment_with_tuned_model(kind, name, source)
    llm = load_llm()
    return generate_comment_with_llm(llm, kind, name, source)


def load_llm():
    return _load_base_llm()


def generate_comment_with_llm(llm, kind: str, name: str, source: str) -> str:
    messages = build_comment_messages(kind, name, source)
    response = llm.create_chat_completion(
        messages=messages,
        temperature=0.15,
        top_p=0.9,
        max_tokens=80,
        stop=["\n\n", "```"],
    )
    text = response["choices"][0]["message"]["content"]
    return _clean_comment(text)


def generate_comment_with_tuned_model(kind: str, name: str, source: str) -> str:
    import torch

    tokenizer, model = _load_tuned_model()
    messages = build_comment_messages(kind, name, source)
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    encoded = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=int(os.getenv("BTL_TUNED_MODEL_CTX", "4096")),
    ).to(model.device)

    with torch.inference_mode():
        output_ids = model.generate(
            **encoded,
            do_sample=False,
            max_new_tokens=80,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )[0]

    generated_ids = output_ids[encoded["input_ids"].shape[-1] :]
    text = tokenizer.decode(generated_ids, skip_special_tokens=True)
    return _clean_comment(text)

import os
from functools import lru_cache
from typing import Literal

from .prompts import build_comment_messages, build_summary_messages


DEFAULT_BASE_TRANSFORMERS_MODEL = "JetBrains/Mellum2-12B-A2.5B-Instruct"
DEFAULT_TUNED_ADAPTER_REPO = "coolbeanz79/between-the-lines-mellum2-lora"

ModelVariant = Literal["base", "tuned"]


class ModelUnavailableError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load_base_model():
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        raise ModelUnavailableError(
            "Base Mellum2 inference requires torch, transformers, accelerate, and bitsandbytes."
        ) from exc

    model_name = os.getenv("BTL_BASE_MODEL", DEFAULT_BASE_TRANSFORMERS_MODEL)

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
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            trust_remote_code=True,
            device_map="auto",
            quantization_config=quantization_config,
        )
        model.eval()
        return tokenizer, model
    except Exception as exc:
        raise ModelUnavailableError(f"Could not load base Mellum2 model `{model_name}`: {exc}") from exc


def _clean_comment(text: str, language: str = "python") -> str:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    line = line.strip("`").strip()

    if line.startswith("Comment:"):
        line = line.removeprefix("Comment:").strip()

    line = line.removeprefix("//").removeprefix("#").strip()
    prefix = "# " if language == "python" else "// "
    line = prefix + line

    return line[:240].rstrip()


def _clean_summary(text: str) -> str:
    cleaned = " ".join(line.strip().strip("`") for line in text.strip().splitlines() if line.strip())
    if cleaned.startswith("Summary:"):
        cleaned = cleaned.removeprefix("Summary:").strip()
    cleaned = cleaned.strip('"').strip("'").strip()
    if cleaned.startswith("#"):
        cleaned = cleaned.lstrip("# ").strip()
    return cleaned[:500].rstrip()


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
        from peft import PeftModel
    except ImportError as exc:
        raise ModelUnavailableError(
            "Tuned LoRA inference requires peft in addition to the base Transformers runtime."
        ) from exc

    try:
        tokenizer, base_model = _load_base_model()
        model = PeftModel.from_pretrained(base_model, adapter_path_or_repo)
        model.eval()
        return tokenizer, model
    except Exception as exc:
        raise ModelUnavailableError(f"Could not load tuned LoRA adapter `{adapter_path_or_repo}`: {exc}") from exc


def generate_comment(kind: str, name: str, source: str, variant: ModelVariant = "base", language: str = "python") -> str:
    if variant == "tuned":
        return generate_comment_with_tuned_model(kind, name, source, language)
    return generate_comment_with_base_model(kind, name, source, language)


def generate_file_summary(source: str, variant: ModelVariant = "base", language: str = "python") -> str:
    if variant == "tuned":
        tokenizer, model = _load_tuned_model()
        return _generate_text_with_transformers(
            tokenizer,
            model,
            build_summary_messages(source, language),
            "BTL_TUNED_MODEL_CTX",
            max_new_tokens=120,
            cleaner=_clean_summary,
        )

    tokenizer, model = _load_base_model()
    return _generate_text_with_transformers(
        tokenizer,
        model,
        build_summary_messages(source, language),
        "BTL_MODEL_CTX",
        max_new_tokens=120,
        cleaner=_clean_summary,
    )


def _generate_text_with_transformers(
    tokenizer,
    model,
    messages: list[dict[str, str]],
    max_length_env: str,
    max_new_tokens: int,
    cleaner,
) -> str:
    import torch

    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    encoded = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=int(os.getenv(max_length_env, "4096")),
    )

    device = getattr(model, "device", None)
    if device is not None:
        encoded = encoded.to(device)

    with torch.inference_mode():
        output_ids = model.generate(
            **encoded,
            do_sample=False,
            max_new_tokens=max_new_tokens,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )[0]

    generated_ids = output_ids[encoded["input_ids"].shape[-1] :]
    text = tokenizer.decode(generated_ids, skip_special_tokens=True)
    return cleaner(text)


def _generate_comment_with_transformers(tokenizer, model, kind: str, name: str, source: str, max_length_env: str, language: str) -> str:
    return _generate_text_with_transformers(
        tokenizer,
        model,
        build_comment_messages(kind, name, source, language),
        max_length_env,
        max_new_tokens=80,
        cleaner=lambda text: _clean_comment(text, language),
    )


def generate_comment_with_base_model(kind: str, name: str, source: str, language: str = "python") -> str:
    tokenizer, model = _load_base_model()
    return _generate_comment_with_transformers(tokenizer, model, kind, name, source, "BTL_MODEL_CTX", language)


def generate_comment_with_tuned_model(kind: str, name: str, source: str, language: str = "python") -> str:
    tokenizer, model = _load_tuned_model()
    return _generate_comment_with_transformers(tokenizer, model, kind, name, source, "BTL_TUNED_MODEL_CTX", language)

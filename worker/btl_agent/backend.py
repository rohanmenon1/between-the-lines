"""Lazy CPU inference; native memory is released after an idle interval."""

import json
from pathlib import Path
import threading

from .types import Configuration, WorkerError


def decision_schema(allowed_paths, task=None, evidence_ids=None):
    """Each action has exactly the fields its executor needs."""
    if task == "comment_verification":
        return {"type": "object", "additionalProperties": False,
                "properties": {"supported": {"type": "boolean"},
                               "reason": {"type": "string", "maxLength": 300}},
                "required": ["supported", "reason"]}
    branches = []
    synthesis = task == "comment_synthesis"
    actions = ["skip", "synthesize_comment" if synthesis else "propose_comment", "flag_mismatch"]
    if synthesis and evidence_ids == []:
        actions.remove("synthesize_comment")
    if allowed_paths:
        actions.append("read_context")
    for action in actions:
        properties = {
            "action": {"const": action},
            "reason": {"type": "string", "maxLength": 160},
        }
        if action == "propose_comment":
            properties["comment"] = {"type": "string", "minLength": 1, "maxLength": 180}
        elif action == "synthesize_comment":
            # Emit the comment before its short explanation. Tiny models can
            # otherwise continue a truncated reason inside the comment field.
            properties.pop("reason")
            properties["comment"] = {"type": "string", "minLength": 1, "maxLength": 240}
            properties["promptEvidence"] = {
                "type": "object", "additionalProperties": False,
                # Large maxLength values expand into an unusable native
                # grammar. Output tokens are bounded; grounding.py separately
                # enforces the 2,000-character source-evidence limit.
                "properties": {"quote": {"type": "string", "minLength": 1}},
                "required": ["quote"],
            }
            if evidence_ids is not None:
                properties["promptEvidence"]["properties"] = {"passageId": {"type": "string", "enum": evidence_ids}}
                properties["promptEvidence"]["required"] = ["passageId"]
            properties["reason"] = {"type": "string", "maxLength": 80}
        elif action == "read_context":
            properties["path"] = {"type": "string", "enum": list(allowed_paths)}
        branches.append({"type": "object", "additionalProperties": False,
                         "properties": properties, "required": list(properties)})
    return {"oneOf": branches}


class LlamaBackend:
    """One serial model instance. The idle timer never interrupts inference."""

    def __init__(self):
        self.configuration = Configuration()
        self._model = None
        self._lock = threading.RLock()
        self._timer = None
        self._generation = 0

    def configure(self, configuration):
        with self._lock:
            if configuration != self.configuration:
                self.unload()
                self.configuration = configuration

    def identity(self):
        path = Path(self.configuration.model_path)
        try:
            stat = path.stat()
            return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        except OSError:
            return (str(path), "missing")

    def status(self):
        with self._lock:
            return {
                "protocolVersion": 1, "modelLoaded": self._model is not None,
                "modelPath": self.configuration.model_path, "backend": "llama.cpp CPU", "provider": "local",
            }

    def _load(self):
        if self._model is not None:
            return
        if not self.configuration.model_path or not Path(self.configuration.model_path).is_file():
            raise WorkerError("model_unavailable", "Download and select a local GGUF model first.")
        try:
            from llama_cpp import Llama

            self._model = Llama(
                model_path=self.configuration.model_path,
                n_ctx=self.configuration.context_tokens,
                n_threads=self.configuration.threads,
                n_threads_batch=self.configuration.threads,
                n_batch=min(128, self.configuration.context_tokens),
                n_gpu_layers=0, use_mmap=True, use_mlock=False, verbose=False,
            )
        except Exception as exc:
            self.unload()
            raise WorkerError("runtime_unavailable", "The local inference runtime could not load this model.") from exc

    def generate(self, system, payload):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._generation += 1
            generation = self._generation
            try:
                self._load()
                # The curated baseline uses Qwen ChatML. Explicit formatting lets
                # us enforce the actual token budget before starting generation.
                user = json.dumps(payload, ensure_ascii=False)
                # Keep source text from introducing ChatML control tokens.
                user = user.replace("<", "\\u003c").replace(">", "\\u003e")
                prompt = (
                    "<|im_start|>system\n" + system + "<|im_end|>\n"
                    "<|im_start|>user\n" + user + "<|im_end|>\n"
                    "<|im_start|>assistant\n"
                )
                qwen35 = getattr(self._model, "metadata", {}).get("general.architecture") == "qwen35"
                if qwen35:
                    # Match Qwen3.5's non-thinking chat template: no hidden reasoning tokens.
                    prompt += "<think>\n\n</think>\n\n"
                count = len(self._model.tokenize(prompt.encode("utf-8"), add_bos=False, special=True))
                if count + self.configuration.max_output_tokens >= self.configuration.context_tokens:
                    raise WorkerError("context_limit", "This declaration exceeds the configured context budget.")
                from llama_cpp import LlamaGrammar

                grammar = LlamaGrammar.from_json_schema(
                    json.dumps(decision_schema(payload.get("allowedContextFiles", []), payload.get("task"),
                                               [item["id"] for item in payload["evidenceChoices"]]
                                               if "evidenceChoices" in payload else None)), verbose=False,
                )
                decoding = {"temperature": 0}
                if qwen35:
                    decoding.update(top_p=1.0, top_k=0, min_p=0, repeat_penalty=1.0, seed=42)
                result = self._model.create_completion(
                    prompt=prompt, max_tokens=self.configuration.max_output_tokens,
                    **decoding, grammar=grammar,
                    stop=["<|im_end|>", "<|endoftext|>"],
                )
                usage = result.get("usage", {})
                try:
                    decision = json.loads(result["choices"][0]["text"])
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                    raise WorkerError("invalid_model_output", "The model did not finish a valid decision.") from exc
                return decision, {
                    "inputTokens": usage.get("prompt_tokens", count),
                    "outputTokens": usage.get("completion_tokens", 0),
                }
            except WorkerError:
                raise
            except Exception as exc:
                raise WorkerError("inference_failed", "Local inference failed; no edit was produced.") from exc
            finally:
                self._timer = threading.Timer(self.configuration.idle_seconds, self._unload_if_idle, (generation,))
                self._timer.daemon = True
                self._timer.start()

    def _unload_if_idle(self, generation):
        with self._lock:
            if generation == self._generation:
                self.unload()

    def unload(self):
        with self._lock:
            self._generation += 1
            if self._timer:
                self._timer.cancel()
                self._timer = None
            model, self._model = self._model, None
            if model is not None:
                model.close()

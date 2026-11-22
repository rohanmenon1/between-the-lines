"""Small internal records and public protocol errors."""

from dataclasses import dataclass, field
import hashlib
import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit


class WorkerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_lines(text: str) -> list[str]:
    # str.splitlines also splits form feeds and Unicode separators, whereas
    # source parsers and editor line numbers only use CR/LF line endings.
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


def validate_evidence(prompt, evidence):
    """Check exact original text and UTF-16 ranges, not semantic support."""
    if not isinstance(prompt, str) or not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
        raise WorkerError("invalid_evidence", "Provide bounded evidence from the original prompt.")
    prompt_hash = digest(prompt)
    encoded = prompt.encode("utf-16-le")
    normalized = []
    for item in evidence:
        if not isinstance(item, dict):
            raise WorkerError("invalid_evidence", "Evidence must contain an exact prompt span.")
        quote, start, end = item.get("quote"), item.get("start"), item.get("end")
        if (not isinstance(quote, str) or not quote or len(quote) > 2000
                or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(encoded) // 2
                or item.get("promptHash", prompt_hash) != prompt_hash):
            raise WorkerError("invalid_evidence", "Evidence differs from its original prompt.")
        try:
            actual = encoded[start * 2:end * 2].decode("utf-16-le")
            before = encoded[:start * 2].decode("utf-16-le")
            after = encoded[end * 2:].decode("utf-16-le")
        except UnicodeError as exc:
            raise WorkerError("invalid_evidence", "Evidence splits a Unicode character.") from exc
        # Whole prompt lines retain local negation/conditions. This remains a
        # provenance check; neighboring lines can still alter their meaning.
        if (actual != quote or re.search(r"[^\r\n]*\Z", before).group().strip()
                or re.match(r"[^\r\n]*", after).group().strip()
                or any(unicodedata.category(char).startswith("C") and char not in "\r\n\t" for char in quote)):
            raise WorkerError("invalid_evidence", "Evidence must preserve a complete original prompt line.")
        normalized.append({"quote": quote, "start": start, "end": end, "promptHash": prompt_hash})
    if sum(len(item["quote"]) for item in normalized) > 8000:
        raise WorkerError("invalid_evidence", "Evidence exceeds the bounded context budget.")
    return normalized


def validate_generation(value):
    if (not isinstance(value, dict) or value.get("provider") not in {"local", "custom"}
            or not isinstance(value.get("model"), str) or not value["model"].strip()
            or len(value["model"]) > 256
            or any(unicodedata.category(char).startswith("C") for char in value["model"])):
        raise WorkerError("invalid_evidence", "A generated comment needs its provider and model identity.")
    return {"provider": value["provider"], "model": value["model"]}


def validate_comment_text(value, language):
    """Validate one generated line without treating its meaning as verified."""
    prefix = "# " if language == "python" else "// "
    if not isinstance(value, str) or not value.startswith(prefix):
        raise WorkerError("unsafe_proposal", "Generated annotations must be line comments.")
    content = value[len(prefix):]
    if (not content or content != content.strip() or len(content) > 240 or "\\u" in content
            or any(unicodedata.category(char).startswith("C") for char in content)
            or "\u2028" in content or "\u2029" in content
            or language == "python" and (content.startswith("!")
                or re.search(r"coding\s*[:=]|noqa\b|type\s*:|fmt\s*:|pylint\s*:|ruff\s*:", content))):
        raise WorkerError("unsafe_proposal", "Generated annotation text is unsafe or excessive.")
    return value


@dataclass(frozen=True)
class Declaration:
    symbol_id: str
    name: str
    kind: str
    start_line: int
    end_line: int
    source: str
    code_hash: str
    has_docstring: bool = False


@dataclass(frozen=True)
class Configuration:
    model_path: str = ""
    threads: int = 2
    context_tokens: int = 2048
    max_output_tokens: int = 160
    idle_seconds: int = 60
    provider: str = "local"
    endpoint: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)

    @classmethod
    def from_params(cls, params: dict):
        provider = params.get("provider", "local")
        if not isinstance(provider, str) or provider not in {"local", "custom"}:
            raise WorkerError("invalid_request", "provider must be local or custom.")
        model_path = params.get("modelPath", "")
        if not isinstance(model_path, str) or len(model_path) > 4096:
            raise WorkerError("invalid_request", "modelPath must be a local path.")
        bounds = {
            "threads": (1, 8, 2),
            "contextTokens": (512, 4096, 2048),
            "maxOutputTokens": (32, 256, 160),
            "idleSeconds": (1, 300, 60),
        }
        values = []
        for name, (minimum, maximum, default) in bounds.items():
            value = params.get(name, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise WorkerError("invalid_request", f"{name} must be {minimum}–{maximum}.")
            values.append(value)
        endpoint, model, api_key = "", "", ""
        if provider == "custom":
            endpoint = validate_endpoint(params.get("endpoint"))
            model = params.get("model", "")
            if not isinstance(model, str) or not model.strip() or len(model) > 256:
                raise WorkerError("invalid_request", "Provide the model name for the custom endpoint.")
            api_key = params.get("apiKey", "")
            if (not isinstance(api_key, str) or len(api_key) > 8192
                    or any(ord(char) < 33 or ord(char) > 126 for char in api_key)):
                raise WorkerError("invalid_request", "The API key must contain printable characters without whitespace.")
            model = model.strip()
            model_path = ""
        return cls(model_path, *values, provider, endpoint, model, api_key)


def validate_endpoint(value):
    """Accept an explicit API URL without credentials or redirectable secrets."""
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise WorkerError("invalid_request", "Provide a valid HTTPS chat-completions URL.")
    try:
        parsed = urlsplit(value)
        # Accessing port also rejects malformed numeric ports.
        port = parsed.port
        host = parsed.hostname
        if not host or parsed.scheme not in {"https", "http"} or port == 0:
            raise ValueError("Unsupported endpoint")
        if (parsed.username is not None or parsed.password is not None
                or "?" in value or "#" in value or "\\" in value):
            raise ValueError("Endpoint must not contain credentials, query, or fragment")
        loopback = host.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if parsed.scheme == "http" and not loopback:
            raise ValueError("Remote endpoints require HTTPS")
    except ValueError as exc:
        raise WorkerError("invalid_request", "Use HTTPS, or HTTP on loopback, without URL credentials, query, or fragment.") from exc
    return value.rstrip("/")

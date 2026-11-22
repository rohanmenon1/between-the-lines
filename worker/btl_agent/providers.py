"""Explicit inference selection; parsing and source edits never leave the worker."""

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .backend import LlamaBackend, decision_schema
from .types import Configuration, WorkerError, digest


MAX_RESPONSE_BYTES = 1024 * 1024
MAX_PROMPT_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 30
DECISION_SCHEMA_VERSION = 3


class NoRedirect(HTTPRedirectHandler):
    """Never forward source text or authorization to a redirect target."""

    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class CustomBackend:
    """One explicitly selected chat-completions endpoint, with no fallback."""

    def __init__(self, opener=None):
        self.configuration = Configuration()
        self._opener = opener if opener is not None else build_opener(NoRedirect())

    def configure(self, configuration):
        self.configuration = configuration

    def identity(self):
        settings = self.configuration
        return ("custom", settings.endpoint, settings.model, DECISION_SCHEMA_VERSION,
                digest(settings.api_key))

    def status(self):
        return {"protocolVersion": 1, "provider": "custom", "modelLoaded": False,
                "modelPath": "", "backend": "Custom chat API"}

    def unload(self):
        # Closing the worker cancels in-flight HTTP work; there are no weights.
        pass

    def generate(self, system, payload):
        settings = self.configuration
        schema = decision_schema(payload.get("allowedContextFiles", []), payload.get("task"),
                                 [item["id"] for item in payload["evidenceChoices"]]
                                 if "evidenceChoices" in payload else None)
        messages = [
            {"role": "system", "content": system + "\nReturn only a JSON object matching this schema:\n" + json.dumps(schema)},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        if len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) > MAX_PROMPT_BYTES:
            raise WorkerError("context_limit", "This declaration exceeds the custom endpoint input budget.")
        body = json.dumps({
            "model": settings.model, "messages": messages,
            "temperature": 0, "max_tokens": settings.max_output_tokens,
            "response_format": {"type": "json_object"}, "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if settings.api_key:
            headers["Authorization"] = "Bearer " + settings.api_key
        request = Request(settings.endpoint, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                content = response.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                raise WorkerError("invalid_model_output", "The custom endpoint response exceeded the size limit.")
            result = json.loads(content)
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Expected JSON message text")
            decision = json.loads(content)
            if not isinstance(decision, dict):
                raise TypeError("Expected a JSON decision object")
            usage = result.get("usage") or {}
            return decision, {
                "inputTokens": valid_tokens(usage.get("prompt_tokens")),
                "outputTokens": valid_tokens(usage.get("completion_tokens")),
            }
        except WorkerError:
            raise
        except HTTPError as exc:
            # Do not display response bodies, request URLs, headers, or secrets.
            exc.close()
            if exc.code in {401, 403}:
                raise WorkerError("provider_auth", "The custom endpoint rejected the API credentials.") from None
            if exc.code == 429:
                raise WorkerError("provider_rate_limit", "The custom endpoint rate limit was reached; try again later.") from None
            if 300 <= exc.code < 400:
                raise WorkerError("provider_redirect", "The custom endpoint redirected the request. Use its final HTTPS URL.") from None
            if exc.code in {400, 404, 405, 422}:
                raise WorkerError("provider_request", "The endpoint rejected the request. Check its model name and chat-completions URL with JSON-mode support.") from None
            raise WorkerError("provider_unavailable", "The custom endpoint could not complete the request.") from None
        except (TimeoutError, socket.timeout):
            raise WorkerError("provider_timeout", "The custom endpoint timed out; no edit was produced.") from None
        except (URLError, OSError):
            raise WorkerError("provider_unavailable", "The custom endpoint could not be reached; check its configuration.") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError):
            raise WorkerError("invalid_model_output", "The custom endpoint did not return a complete JSON decision.") from None


def valid_tokens(value):
    return value if type(value) is int and value >= 0 else 0


class RoutingBackend:
    """Switch providers only on configuration; never retry through another one."""

    def __init__(self, local=None, custom=None):
        self.local = local if local is not None else LlamaBackend()
        self.custom = custom if custom is not None else CustomBackend()
        self.active = self.local
        self.configuration = Configuration()

    def configure(self, configuration):
        selected = self.local if configuration.provider == "local" else self.custom
        if selected is not self.active:
            self.active.unload()
        self.configuration = configuration
        self.active = selected
        self.active.configure(configuration)

    def identity(self):
        return (self.configuration.provider, self.active.identity(), DECISION_SCHEMA_VERSION)

    def status(self):
        return self.active.status()

    def generate(self, system, payload):
        return self.active.generate(system, payload)

    def unload(self):
        self.local.unload()
        self.custom.unload()

"""Provider boundary tests use an injected HTTP fixture; no requests leave the process."""

import io
import json
from urllib.error import HTTPError, URLError
import unittest

from btl_agent.__main__ import serve
from btl_agent.providers import CustomBackend, MAX_PROMPT_BYTES, MAX_RESPONSE_BYTES, NoRedirect, RoutingBackend
from btl_agent.types import Configuration, WorkerError, validate_endpoint


SECRET = "private-test-key"
ENDPOINT = "https://provider.example/v1/chat/completions"


def custom_configuration(**overrides):
    return Configuration.from_params({
        "provider": "custom", "endpoint": ENDPOINT,
        "model": "configured-model", "apiKey": SECRET, **overrides,
    })


def completion(decision=None, usage=None):
    if decision is None:
        decision = {"action": "skip", "reason": "Insufficient explicit intent."}
    return json.dumps({
        "choices": [{"message": {"content": json.dumps(decision)}}],
        "usage": usage if usage is not None else {"prompt_tokens": 15, "completion_tokens": 8},
    }).encode("utf-8")


class FixtureResponse(io.BytesIO):
    def read(self, size=-1):
        self.read_limit = size
        return super().read(size)


class FixtureOpener:
    def __init__(self, response=None, error=None):
        self.response = response if response is not None else completion()
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        self.stream = FixtureResponse(self.response)
        return self.stream


class ProviderConfigurationTests(unittest.TestCase):
    def test_local_defaults_remain_unchanged_and_custom_needs_no_model_file(self):
        local = Configuration.from_params({})
        self.assertEqual((local.provider, local.model_path, local.threads), ("local", "", 2))
        custom = custom_configuration(modelPath="ignored.gguf")
        self.assertEqual(custom.model_path, "")
        self.assertEqual(custom.endpoint, ENDPOINT)
        self.assertNotIn(SECRET, repr(custom))

    def test_only_https_or_loopback_http_without_url_secrets(self):
        for value in (ENDPOINT, "http://127.0.0.1:8000/v1/chat/completions", "http://localhost:8080/api", "http://[::1]:8080/api"):
            self.assertEqual(validate_endpoint(value + "/"), value)
        for value in (
            None, "", "file:///secret", "http://remote.example/api", "http://0.0.0.0/api",
            "https://user:password@provider.example/api", ENDPOINT + "?key=secret",
            ENDPOINT + "?", ENDPOINT + "#part", "https://provider.example:invalid/api",
            "https://provider.example:0/api", "https://provider.example\\other/path",
            "https://provider.example/\nheader",
        ):
            with self.subTest(value=value), self.assertRaises(WorkerError) as error:
                validate_endpoint(value)
            self.assertEqual(error.exception.code, "invalid_request")
            self.assertNotIn("password", str(error.exception))

    def test_invalid_provider_model_and_key_are_actionable_without_echoing_values(self):
        cases = ({"provider": []}, {"provider": "other"}, {"model": ""},
                 {"model": 3}, {"apiKey": SECRET + "\nInjected"}, {"apiKey": 3})
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(WorkerError) as error:
                custom_configuration(**overrides)
            self.assertEqual(error.exception.code, "invalid_request")
            self.assertNotIn(SECRET, str(error.exception))


class CustomProviderTests(unittest.TestCase):
    def backend(self, fixture=None, **configuration):
        fixture = fixture if fixture is not None else FixtureOpener()
        backend = CustomBackend(fixture)
        backend.configure(custom_configuration(**configuration))
        return backend, fixture

    def test_request_uses_selected_url_model_and_key_with_bounded_json_response(self):
        backend, fixture = self.backend()
        payload = {"currentSource": "def caf\u00e9():\n    return 1\n", "allowedContextFiles": []}
        decision, usage = backend.generate("Only explicit instructions count.", payload)
        request, timeout = fixture.calls[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + SECRET)
        self.assertEqual(body["model"], "configured-model")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertFalse(body["stream"])
        self.assertEqual(body["max_tokens"], 160)
        self.assertEqual(json.loads(body["messages"][1]["content"]), payload)
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertEqual(timeout, 30)
        self.assertEqual(fixture.stream.read_limit, MAX_RESPONSE_BYTES + 1)
        self.assertEqual((decision["action"], usage), ("skip", {"inputTokens": 15, "outputTokens": 8}))

    def test_keys_are_optional_and_identity_changes_without_exposing_them(self):
        backend, fixture = self.backend(apiKey="")
        first = backend.identity()
        backend.generate("system", {})
        self.assertIsNone(fixture.calls[0][0].get_header("Authorization"))
        backend.configure(custom_configuration())
        self.assertNotEqual(first, backend.identity())
        self.assertNotIn(SECRET, str(backend.identity()))
        self.assertNotIn(SECRET, json.dumps(backend.status()))

    def test_invalid_model_responses_are_rejected_without_leaking_server_text(self):
        responses = [b"not json " + SECRET.encode(), b"{}", b'{"choices": []}',
                     json.dumps({"choices": [{"message": {"content": []}}]}).encode(),
                     json.dumps({"choices": [{"message": {"content": "```json\n{}\n```"}}]}).encode(),
                     completion(decision=["not", "an", "object"])]
        for response in responses:
            backend, _ = self.backend(FixtureOpener(response=response))
            with self.subTest(response=response[:30]), self.assertRaises(WorkerError) as error:
                backend.generate("system", {})
            self.assertEqual(error.exception.code, "invalid_model_output")
            self.assertNotIn(SECRET, str(error.exception))

    def test_input_and_response_caps_prevent_unbounded_work(self):
        backend, fixture = self.backend()
        with self.assertRaises(WorkerError) as error:
            backend.generate("system", {"source": "x" * MAX_PROMPT_BYTES})
        self.assertEqual(error.exception.code, "context_limit")
        self.assertEqual(fixture.calls, [])
        backend, _ = self.backend(FixtureOpener(response=b"x" * (MAX_RESPONSE_BYTES + 1)))
        with self.assertRaises(WorkerError) as error:
            backend.generate("system", {})
        self.assertEqual(error.exception.code, "invalid_model_output")

    def test_http_errors_are_sanitized_and_never_retried(self):
        statuses = {401: "provider_auth", 403: "provider_auth", 429: "provider_rate_limit",
                    400: "provider_request", 404: "provider_request", 500: "provider_unavailable",
                    301: "provider_redirect", 307: "provider_redirect"}
        for status, expected in statuses.items():
            failure = HTTPError(ENDPOINT, status, SECRET, {}, io.BytesIO(SECRET.encode()))
            backend, fixture = self.backend(FixtureOpener(error=failure))
            with self.subTest(status=status), self.assertRaises(WorkerError) as error:
                backend.generate("system", {})
            self.assertEqual(error.exception.code, expected)
            self.assertNotIn(SECRET, str(error.exception))
            self.assertEqual(len(fixture.calls), 1)

    def test_transport_failures_have_safe_errors(self):
        for failure, code in ((TimeoutError(SECRET), "provider_timeout"),
                              (URLError(SECRET), "provider_unavailable"),
                              (OSError(SECRET), "provider_unavailable")):
            backend, fixture = self.backend(FixtureOpener(error=failure))
            with self.subTest(code=code), self.assertRaises(WorkerError) as error:
                backend.generate("system", {})
            self.assertEqual(error.exception.code, code)
            self.assertNotIn(SECRET, str(error.exception))
            self.assertEqual(len(fixture.calls), 1)

    def test_redirect_policy_cannot_forward_authorization_or_source(self):
        backend, fixture = self.backend()
        backend.generate("system", {"source": "private source"})
        request = fixture.calls[0][0]
        for status in (301, 302, 303, 307, 308):
            self.assertIsNone(NoRedirect().redirect_request(request, None, status, "move", {}, "https://other.example/steal"))

    def test_invalid_usage_cannot_corrupt_runtime_metrics(self):
        backend, _ = self.backend(FixtureOpener(response=completion(usage={"prompt_tokens": -2, "completion_tokens": True})))
        _, usage = backend.generate("system", {})
        self.assertEqual(usage, {"inputTokens": 0, "outputTokens": 0})


class RoutingTests(unittest.TestCase):
    def test_custom_configuration_and_inference_never_load_local_weights(self):
        custom = CustomBackend(FixtureOpener())
        router = RoutingBackend(custom=custom)
        router.configure(custom_configuration())
        self.assertEqual(router.status()["provider"], "custom")
        self.assertEqual(router.status()["modelPath"], "")
        self.assertEqual(router.generate("system", {})[0]["action"], "skip")
        self.assertIsNone(router.local._model)
        router.configure(Configuration())
        self.assertEqual(router.status()["provider"], "local")
        router.unload()

    def test_custom_failure_does_not_fall_back_to_local(self):
        custom = CustomBackend(FixtureOpener(error=TimeoutError()))
        router = RoutingBackend(custom=custom)
        router.configure(custom_configuration())
        with self.assertRaises(WorkerError) as error:
            router.generate("system", {})
        self.assertEqual(error.exception.code, "provider_timeout")
        self.assertIsNone(router.local._model)

    def test_worker_protocol_does_not_return_configured_secrets(self):
        configuration = {"provider": "custom", "endpoint": ENDPOINT, "model": "configured-model", "apiKey": SECRET}
        requests = b"".join(json.dumps(item).encode() + b"\n" for item in (
            {"id": "1", "method": "configure", "params": configuration},
            {"id": "2", "method": "status"},
            {"id": "3", "method": "analyze", "params": {
                "uri": "file:///workspace/example.py", "language": "python",
                "source": "def example():\n    return 1\n",
            }},
            {"id": "4", "method": "shutdown"},
        ))
        output = io.StringIO()
        fixture = FixtureOpener()
        serve(io.BytesIO(requests), output, RoutingBackend(custom=CustomBackend(fixture)))
        self.assertNotIn(SECRET, output.getvalue())
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["result"]["provider"], "custom")
        self.assertEqual(responses[2]["result"]["suggestions"], [])
        self.assertEqual(len(fixture.calls), 1)
        self.assertTrue(responses[-1]["result"]["stopped"])


if __name__ == "__main__":
    unittest.main()

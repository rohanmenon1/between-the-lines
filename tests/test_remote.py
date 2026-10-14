"""Check the boundary between the web UI and hosted model service."""

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from btl.model import ModelUnavailableError
from btl.remote import _request_annotation, annotate_remote


PYTHON_SOURCE = 'def value():\n    return "caf\u00e9"\n'
JAVA_SOURCE = "class Value {\n    int value() { return 1; }\n}\n"
URL = "https://example.hf.space"


class RemoteValidationTests(unittest.TestCase):
    def annotate(self, response, source=PYTHON_SOURCE, language="python"):
        with patch("btl.remote._request_annotation", return_value=response):
            return annotate_remote(source, "base", language, URL)

    def test_accepts_comment_additions_for_both_languages(self):
        for source, language, comment in (
            (PYTHON_SOURCE, "python", "# Returns a value.\n"),
            (JAVA_SOURCE, "java", "// Holds a value.\n"),
        ):
            with self.subTest(language=language):
                result = self.annotate(
                    ("Summary", comment + source, "Validated: server check passed."),
                    source,
                    language,
                )
                self.assertTrue(result.valid)
                self.assertEqual(result.annotated_source, comment + source)

    def test_rejects_changed_code_even_when_server_claims_success(self):
        changed = PYTHON_SOURCE.replace('"caf\u00e9"', '"changed"')
        result = self.annotate(("Summary", changed, "Validated: AST unchanged."))
        self.assertFalse(result.valid)
        self.assertEqual(result.annotated_source, PYTHON_SOURCE)

    def test_rejects_removing_a_source_line(self):
        source = "# Existing comment.\n" + PYTHON_SOURCE
        result = self.annotate(("Summary", PYTHON_SOURCE, "Validated:"), source)
        self.assertFalse(result.valid)
        self.assertEqual(result.annotated_source, source)

    def test_rejects_java_unicode_preprocessing(self):
        result = self.annotate(
            ("Summary", "// \\u000a unsafe\n" + JAVA_SOURCE, "Validated:"),
            JAVA_SOURCE,
            "java",
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.annotated_source, JAVA_SOURCE)

    def test_rejects_python_encoding_cookie(self):
        result = self.annotate(
            ("Summary", "# coding: latin-1\n" + PYTHON_SOURCE, "Validated:")
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.annotated_source, PYTHON_SOURCE)

    def test_error_with_unchanged_source_is_not_success(self):
        result = self.annotate(("", PYTHON_SOURCE, "Model unavailable: busy."))
        self.assertFalse(result.valid)
        self.assertEqual(result.status, "Model unavailable: busy.")
        self.assertEqual(result.annotated_source, PYTHON_SOURCE)

    def test_malformed_responses_preserve_source(self):
        for response in (
            None,
            ["only one field"],
            ["summary", None, "Validated:"],
            {"data": []},
        ):
            with self.subTest(response=response):
                result = self.annotate(response)
                self.assertFalse(result.valid)
                self.assertEqual(result.annotated_source, PYTHON_SOURCE)

    def test_invalid_source_does_not_call_the_service(self):
        with patch("btl.remote._request_annotation") as request:
            result = annotate_remote("def broken(:", "base", "python", URL)
        self.assertFalse(result.valid)
        request.assert_not_called()

    def test_comment_inside_string_is_not_a_safe_addition(self):
        source = 'def text():\n    return """hello\nworld"""\n'
        changed = source.replace("world", "# Added inside literal.\nworld")
        result = self.annotate(("Summary", changed, "Validated:"), source)
        self.assertFalse(result.valid)
        self.assertEqual(result.annotated_source, source)


class RemoteTransportTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.config = {"dependencies": [{"api_name": "annotate_multilang"}]}
        self.client.submit.return_value.result.return_value = (
            "Summary",
            PYTHON_SOURCE,
            "Validated:",
        )
        self.factory = Mock(return_value=self.client)
        self.module_patch = patch.dict(
            "sys.modules", {"gradio_client": SimpleNamespace(Client=self.factory)}
        )
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    def test_timeout_cancels_job_and_closes_client(self):
        self.client.submit.return_value.result.side_effect = TimeoutError
        with self.assertRaisesRegex(ModelUnavailableError, "timed out"):
            _request_annotation(PYTHON_SOURCE, "base", "python", URL)
        self.client.submit.return_value.cancel.assert_called_once()
        self.client.close.assert_called_once()

    def test_old_backend_is_reported_before_submitting(self):
        self.client.config = {"dependencies": [{"api_name": "annotate"}]}
        with self.assertRaisesRegex(
            ModelUnavailableError, "Update the Hugging Face Space"
        ):
            _request_annotation(PYTHON_SOURCE, "base", "python", URL)
        self.client.submit.assert_not_called()
        self.client.close.assert_called_once()

    def test_connection_failure_does_not_expose_exception_details(self):
        self.factory.side_effect = RuntimeError(
            "secret credential in upstream exception"
        )
        with self.assertRaises(ModelUnavailableError) as error:
            _request_annotation(PYTHON_SOURCE, "base", "python", URL)
        self.assertNotIn("secret", str(error.exception))

    def test_recursive_space_configuration_is_rejected(self):
        with patch.dict(os.environ, {"SPACE_HOST": "example.hf.space"}):
            with self.assertRaisesRegex(ModelUnavailableError, "cannot call itself"):
                _request_annotation(PYTHON_SOURCE, "base", "python", URL)
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()

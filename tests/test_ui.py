"""UI regressions for uploads, model selection, and API compatibility."""

import codecs
import importlib.util
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

if importlib.util.find_spec("gradio") is not None:
    import app
else:
    app = None
from btl.core import AnnotationResult


@unittest.skipIf(app is None, "Install requirements-replit.txt to run UI tests.")
class UiTests(unittest.TestCase):
    def test_utf8_bom_upload_is_parsed(self):
        source = "def value():\n    return 1\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value.py"
            path.write_bytes(codecs.BOM_UTF8 + source.encode("utf-8"))
            loaded, _, _, status, language = app.load_uploaded_source(path)
        self.assertEqual(loaded, source)
        self.assertIn("Parsed 1 declaration", status)
        self.assertEqual(language, "Python")

    def test_api_keeps_legacy_model_labels_without_dropdown_validation(self):
        components = {
            component["id"]: component for component in app.demo.config["components"]
        }
        endpoints = {
            dependency["api_name"]: dependency
            for dependency in app.demo.config["dependencies"]
            if dependency["api_name"]
        }
        for name in ("annotate", "annotate_multilang"):
            model_component = components[endpoints[name]["inputs"][1]]
            self.assertEqual(model_component["type"], "textbox")
            source_component = components[endpoints[name]["inputs"][0]]
            output_component = components[endpoints[name]["outputs"][1]]
            self.assertEqual(source_component["type"], "textbox")
            self.assertEqual(output_component["type"], "textbox")
        self.assertEqual(len(endpoints["annotate"]["inputs"]), 2)
        self.assertEqual(len(endpoints["annotate_multilang"]["inputs"]), 3)

    def test_remote_mode_uses_remote_service_and_preserves_output_contract(self):
        result = AnnotationResult(
            "Summary", "# Comment.\ndef value(): pass\n", "Validated:", True, 1
        )
        with (
            patch.object(app, "REMOTE_URL", "https://example.hf.space"),
            patch.object(app, "annotate_remote", return_value=result) as remote,
            patch.object(app, "annotate_source") as local,
        ):
            output = app.annotate_code_with_language(
                "def value(): pass\n", "Base Mellum2 (richer)", "Python"
            )
        self.assertEqual(
            output, (result.summary, result.annotated_source, result.status)
        )
        remote.assert_called_once_with(
            "def value(): pass\n", "base", "python", "https://example.hf.space"
        )
        local.assert_not_called()


if __name__ == "__main__":
    unittest.main()

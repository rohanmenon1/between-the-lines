"""Check model selection without loading Transformers or model weights."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from btl import model


class FakeBaseModel:
    adapter_enabled = False


class FakeTunedModel:
    def __init__(self, base):
        self.base = base
        self.base.adapter_enabled = True

    @property
    def adapter_enabled(self):
        return self.base.adapter_enabled

    def eval(self):
        return self

    @contextmanager
    def disable_adapter(self):
        previous = self.base.adapter_enabled
        self.base.adapter_enabled = False
        try:
            yield
        finally:
            self.base.adapter_enabled = previous


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = model._TransformersRuntime(object(), FakeBaseModel())
        self.load_runtime = patch.object(
            model, "_load_transformers_runtime", return_value=self.runtime
        )
        self.load_runtime.start()
        self.addCleanup(self.load_runtime.stop)
        self.peft = SimpleNamespace(
            PeftModel=SimpleNamespace(
                from_pretrained=lambda base, adapter_path: FakeTunedModel(base)
            )
        )
        self.peft_module = patch.dict("sys.modules", {"peft": self.peft})
        self.peft_module.start()
        self.addCleanup(self.peft_module.stop)

    def comment(self, variant):
        return model.generate_comment(
            "function", "value", "def value(): return 1", variant
        )

    def test_base_does_not_load_an_adapter(self):
        with patch.object(
            model, "_generate_text_with_transformers", return_value="# Base comment."
        ):
            self.comment("base")
        self.assertIsNone(self.runtime.tuned_model)
        self.assertFalse(self.runtime.base_model.adapter_enabled)

    def test_base_and_tuned_remain_distinct_after_loading_adapter(self):
        enabled_during_generation = []

        def generate(tokenizer, selected_model, *args, **kwargs):
            enabled_during_generation.append(selected_model.adapter_enabled)
            return "# Comment."

        with patch.object(
            model, "_generate_text_with_transformers", side_effect=generate
        ):
            self.comment("tuned")
            tuned_model = self.runtime.tuned_model
            self.comment("base")
            self.comment("tuned")

        self.assertEqual(enabled_during_generation, [True, False, True])
        self.assertIs(self.runtime.tuned_model, tuned_model)
        self.assertIs(tuned_model.base, self.runtime.base_model)

    def test_summary_uses_the_same_adapter_selection_as_comments(self):
        self.runtime.tuned_model = FakeTunedModel(self.runtime.base_model)
        enabled_during_generation = []

        def generate(tokenizer, selected_model, *args, **kwargs):
            enabled_during_generation.append(selected_model.adapter_enabled)
            return "Summary."

        with patch.object(
            model, "_generate_text_with_transformers", side_effect=generate
        ):
            model.generate_file_summary("def value(): return 1", "base")
            model.generate_file_summary("def value(): return 1", "tuned")

        self.assertEqual(enabled_during_generation, [False, True])

    def test_base_generation_failure_restores_the_adapter(self):
        self.runtime.tuned_model = FakeTunedModel(self.runtime.base_model)

        def fail(tokenizer, selected_model, *args, **kwargs):
            self.assertFalse(selected_model.adapter_enabled)
            raise RuntimeError("generation failed")

        with patch.object(model, "_generate_text_with_transformers", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "generation failed"):
                self.comment("base")
        self.assertTrue(self.runtime.tuned_model.adapter_enabled)

    def test_concurrent_requests_cannot_change_adapter_during_generation(self):
        self.runtime.tuned_model = FakeTunedModel(self.runtime.base_model)
        base_entered = Event()
        release_base = Event()
        tuned_started = Event()
        tuned_entered = Event()
        enabled_during_generation = []

        def generate(tokenizer, selected_model, *args, **kwargs):
            if not selected_model.adapter_enabled:
                base_entered.set()
                if not release_base.wait(5):
                    raise RuntimeError("test did not release base generation")
                enabled_during_generation.append(selected_model.adapter_enabled)
            else:
                tuned_entered.set()
                enabled_during_generation.append(selected_model.adapter_enabled)
            return "# Comment."

        def tuned_request():
            tuned_started.set()
            return self.comment("tuned")

        with patch.object(
            model, "_generate_text_with_transformers", side_effect=generate
        ):
            with ThreadPoolExecutor(max_workers=2) as workers:
                base = workers.submit(self.comment, "base")
                try:
                    self.assertTrue(base_entered.wait(5))
                    tuned = workers.submit(tuned_request)
                    self.assertTrue(tuned_started.wait(5))
                    self.assertFalse(tuned_entered.is_set())
                finally:
                    release_base.set()
                base.result(timeout=5)
                tuned.result(timeout=5)

        self.assertEqual(enabled_during_generation, [False, True])
        self.assertTrue(self.runtime.tuned_model.adapter_enabled)


if __name__ == "__main__":
    unittest.main()

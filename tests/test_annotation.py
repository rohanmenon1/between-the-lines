"""Focused checks for the source-preservation boundary."""

import unittest
from unittest.mock import patch

from btl.annotate import annotate_python, semantic_ast_dump
from btl.java import (
    annotate_java,
    collect_java_blocks,
    java_syntax_fingerprint,
    parse_java,
)


JAVA_SOURCE = """public class Calculator {
    public int add(int left, int right) {
        return left + right;
    }
}
"""


class JavaAnnotationTests(unittest.TestCase):
    def test_comment_only_annotation_preserves_java_tree(self):
        with (
            patch(
                "btl.java.generate_summary",
                return_value=("Calculator adds values.", []),
            ),
            patch("btl.java.generate_comment", return_value="// Adds two integers."),
        ):
            result = annotate_java(JAVA_SOURCE)

        self.assertTrue(result.valid)
        self.assertEqual(result.block_count, 2)
        self.assertIn(
            "// Adds two integers.\n    public int add", result.annotated_source
        )
        self.assertEqual(
            java_syntax_fingerprint(JAVA_SOURCE),
            java_syntax_fingerprint(result.annotated_source),
        )

    def test_java_tree_detects_changed_code_and_invalid_syntax(self):
        changed = JAVA_SOURCE.replace("left + right", "left - right")
        self.assertNotEqual(
            java_syntax_fingerprint(JAVA_SOURCE), java_syntax_fingerprint(changed)
        )
        with self.assertRaises(SyntaxError):
            parse_java("class Broken { void run( { }")

    def test_same_line_declaration_is_skipped(self):
        source = "class Compact { int add(int a, int b) { return a + b; } }\n"
        blocks = collect_java_blocks(parse_java(source), source)
        self.assertEqual(
            [(block.kind, block.name) for block in blocks], [("class", "Compact")]
        )

    def test_rejects_unicode_escape_in_generated_comment(self):
        with (
            patch("btl.java.generate_summary", return_value=("Summary", [])),
            patch("btl.java.generate_comment", return_value="// unsafe \\u000a code"),
        ):
            result = annotate_java(JAVA_SOURCE)
        self.assertFalse(result.valid)
        self.assertEqual(result.annotated_source, JAVA_SOURCE)

    def test_tuned_model_is_python_only(self):
        result = annotate_java(JAVA_SOURCE, "tuned")
        self.assertFalse(result.valid)
        self.assertIn("Python", result.status)

    def test_repeated_run_does_not_duplicate_java_comments(self):
        with (
            patch("btl.java.generate_summary", return_value=("Summary", [])),
            patch(
                "btl.java.generate_comment", return_value="// Explains the declaration."
            ) as generate,
        ):
            first = annotate_java(JAVA_SOURCE)
            second = annotate_java(first.annotated_source)

        self.assertTrue(first.valid)
        self.assertTrue(second.valid)
        self.assertEqual(first.annotated_source, second.annotated_source)
        self.assertEqual(generate.call_count, 2)

    def test_javadoc_prevents_redundant_method_comment(self):
        source = JAVA_SOURCE.replace(
            "    public int add", "    /** Adds two integers. */\n    public int add"
        )
        with (
            patch("btl.java.generate_summary", return_value=("Summary", [])),
            patch(
                "btl.java.generate_comment", return_value="// Explains the declaration."
            ) as generate,
        ):
            result = annotate_java(source)

        self.assertTrue(result.valid)
        self.assertEqual(generate.call_count, 1)
        self.assertIn(
            "/** Adds two integers. */\n    public int add", result.annotated_source
        )

    def test_generation_failure_does_not_insert_java_todo(self):
        with (
            patch("btl.java.generate_summary", return_value=("Summary", [])),
            patch("btl.java.generate_comment", side_effect=RuntimeError("failed")),
        ):
            result = annotate_java(JAVA_SOURCE)

        self.assertTrue(result.valid)
        self.assertEqual(result.annotated_source, JAVA_SOURCE)
        self.assertNotIn("TODO(model)", result.annotated_source)
        self.assertIn("model generation failed", result.status)

    def test_local_model_uses_heuristic_summary_for_java(self):
        with (
            patch(
                "btl.core.generate_file_summary",
                side_effect=AssertionError("summary inference called"),
            ),
            patch(
                "btl.java.generate_comment", return_value="// Explains the declaration."
            ) as generate,
        ):
            result = annotate_java(JAVA_SOURCE, "local")

        self.assertTrue(result.valid)
        self.assertIn("Calculator", result.summary)
        self.assertEqual(result.notes, ())
        self.assertEqual(
            {call.kwargs["variant"] for call in generate.call_args_list}, {"local"}
        )


class PythonAnnotationTests(unittest.TestCase):
    def test_encoding_cookie_cannot_change_byte_decoding(self):
        source = 'def value():\n    return "caf\u00e9"\n'
        with (
            patch("btl.annotate.generate_summary", return_value=("Summary", [])),
            patch("btl.annotate.generate_comment", return_value="# coding: latin-1"),
        ):
            result = annotate_python(source)
        self.assertFalse(result.valid)
        self.assertEqual(result.annotated_source, source)

    def test_comment_only_annotation_preserves_line_endings(self):
        source = 'def greet():\r\n\treturn "hello"\r\n'
        with (
            patch("btl.annotate.generate_summary", return_value=("Summary", [])),
            patch("btl.annotate.generate_comment", return_value="# Greets a caller."),
        ):
            result = annotate_python(source)

        self.assertTrue(result.valid)
        self.assertEqual(
            semantic_ast_dump(source), semantic_ast_dump(result.annotated_source)
        )
        self.assertEqual(
            result.annotated_source.count("\r\n"), source.count("\r\n") + 1
        )

    def test_docstring_and_existing_comment_prevent_redundant_comments(self):
        source = '# Explains first.\ndef first():\n    return 1\n\ndef second():\n    """Explains second."""\n    return 2\n'
        with (
            patch("btl.annotate.generate_summary", return_value=("Summary", [])),
            patch("btl.annotate.generate_comment") as generate,
        ):
            result = annotate_python(source)

        self.assertTrue(result.valid)
        self.assertEqual(result.annotated_source, source)
        generate.assert_not_called()

    def test_decorated_function_annotation_is_valid_and_idempotent(self):
        source = '@wrap\n@logged("event")\nasync def perform():\n    return 1\n'
        with (
            patch("btl.annotate.generate_summary", return_value=("Summary", [])),
            patch(
                "btl.annotate.generate_comment", return_value="# Performs an action."
            ) as generate,
        ):
            first = annotate_python(source)
            second = annotate_python(first.annotated_source)

        self.assertTrue(first.valid)
        self.assertTrue(second.valid)
        self.assertTrue(
            first.annotated_source.startswith("# Performs an action.\n@wrap\n")
        )
        self.assertEqual(first.annotated_source, second.annotated_source)
        self.assertEqual(
            semantic_ast_dump(source), semantic_ast_dump(first.annotated_source)
        )
        generate.assert_called_once()

    def test_generation_failure_does_not_insert_python_todo(self):
        source = "def calculate():\n    return 1\n"
        with (
            patch("btl.annotate.generate_summary", return_value=("Summary", [])),
            patch("btl.annotate.generate_comment", side_effect=RuntimeError("failed")),
        ):
            result = annotate_python(source)

        self.assertTrue(result.valid)
        self.assertEqual(result.annotated_source, source)
        self.assertNotIn("TODO(model)", result.annotated_source)
        self.assertIn("model generation failed", result.status)

    def test_local_model_uses_heuristic_summary_for_python(self):
        source = "def calculate():\n    return 1\n"
        with (
            patch(
                "btl.core.generate_file_summary",
                side_effect=AssertionError("summary inference called"),
            ),
            patch(
                "btl.annotate.generate_comment", return_value="# Calculates a value."
            ) as generate,
        ):
            result = annotate_python(source, "local")

        self.assertTrue(result.valid)
        self.assertIn("calculate", result.summary)
        self.assertEqual(generate.call_args.kwargs["variant"], "local")


if __name__ == "__main__":
    unittest.main()

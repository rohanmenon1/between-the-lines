"""Focused checks for the source-preservation boundary."""

import unittest
from unittest.mock import patch

from btl.annotate import annotate_python, semantic_ast_dump
from btl.java import annotate_java, collect_java_blocks, java_syntax_fingerprint, parse_java


JAVA_SOURCE = """public class Calculator {
    public int add(int left, int right) {
        return left + right;
    }
}
"""


class JavaAnnotationTests(unittest.TestCase):
    def test_comment_only_annotation_preserves_java_tree(self):
        with patch("btl.java.generate_summary", return_value=("Calculator adds values.", [])), patch(
            "btl.java.generate_comment", return_value="// Adds two integers."
        ):
            result = annotate_java(JAVA_SOURCE)

        self.assertTrue(result.valid)
        self.assertEqual(result.block_count, 2)
        self.assertIn("// Adds two integers.\n    public int add", result.annotated_source)
        self.assertEqual(java_syntax_fingerprint(JAVA_SOURCE), java_syntax_fingerprint(result.annotated_source))

    def test_java_tree_detects_changed_code_and_invalid_syntax(self):
        changed = JAVA_SOURCE.replace("left + right", "left - right")
        self.assertNotEqual(java_syntax_fingerprint(JAVA_SOURCE), java_syntax_fingerprint(changed))
        with self.assertRaises(SyntaxError):
            parse_java("class Broken { void run( { }")

    def test_same_line_declaration_is_skipped(self):
        source = "class Compact { int add(int a, int b) { return a + b; } }\n"
        blocks = collect_java_blocks(parse_java(source), source)
        self.assertEqual([(block.kind, block.name) for block in blocks], [("class", "Compact")])

    def test_rejects_unicode_escape_in_generated_comment(self):
        with patch("btl.java.generate_summary", return_value=("Summary", [])), patch(
            "btl.java.generate_comment", return_value="// unsafe \\u000a code"
        ):
            result = annotate_java(JAVA_SOURCE)
        self.assertFalse(result.valid)
        self.assertEqual(result.annotated_source, JAVA_SOURCE)

    def test_tuned_model_is_python_only(self):
        result = annotate_java(JAVA_SOURCE, "tuned")
        self.assertFalse(result.valid)
        self.assertIn("Python", result.status)


class PythonAnnotationTests(unittest.TestCase):
    def test_comment_only_annotation_preserves_docstrings_and_line_endings(self):
        source = 'def greet():\r\n\t"""Say hello."""\r\n\treturn "hello"\r\n'
        with patch("btl.annotate.generate_summary", return_value=("Summary", [])), patch(
            "btl.annotate.generate_comment", return_value="# Greets a caller."
        ):
            result = annotate_python(source)

        self.assertTrue(result.valid)
        self.assertIn('"""Say hello."""\r\n', result.annotated_source)
        self.assertEqual(semantic_ast_dump(source), semantic_ast_dump(result.annotated_source))
        self.assertEqual(result.annotated_source.count("\r\n"), source.count("\r\n") + 1)


if __name__ == "__main__":
    unittest.main()

import unittest

from btl_agent.grounding import CONTEXT_PREFIX, MAX_EXCERPT_LENGTH, build_grounded_comment
from btl_agent.types import WorkerError


class GroundingTests(unittest.TestCase):
    def grounded(self, passage, prompt=None, symbol="rank"):
        return build_grounded_comment(
            {"action": "propose_comment", "comment": passage, "reason": "Candidate context"},
            prompt if prompt is not None else passage,
            symbol,
        )

    def unsupported(self, passage, prompt=None, symbol="rank"):
        with self.assertRaises(WorkerError) as error:
            self.grounded(passage, prompt, symbol)
        self.assertEqual(error.exception.code, "unsupported_intent")

    def test_quotes_the_user_instead_of_claiming_implementation(self):
        passage = "rank must preserve input order for equal scores."
        result = self.grounded(passage)
        self.assertEqual(result, "User context: " + passage)
        self.assertNotIn("Uses stable sorting", result)

    def test_preserves_negation_and_conditions(self):
        passage = "rank must not change equal-score order unless reverse=True."
        self.assertEqual(self.grounded(passage), CONTEXT_PREFIX + passage)

    def test_rejects_a_fabricated_rationale(self):
        self.unsupported(
            "rank uses stable sorting to make reports deterministic.",
            "rank must preserve input order for equal scores.",
        )

    def test_rejects_clipping_a_negation(self):
        self.unsupported("rank may reorder ties.", "Do not assume rank may reorder ties.")

    def test_rejects_clipping_a_condition(self):
        self.unsupported("rank must reverse ties", "rank must reverse ties only when reverse=True.")

    def test_rejects_clipping_one_sentence_from_a_line(self):
        self.unsupported("rank keeps ties stable.", "rank keeps ties stable. Preserve that during refactors.")

    def test_requires_exact_declaration_reference(self):
        self.unsupported("Preserve the order of equal scores.")
        self.unsupported("rankings must preserve input order.")
        self.unsupported("other_rank must preserve input order.")

    def test_accepts_an_explicit_identifier_in_backticks(self):
        passage = "`rank()` must retain the order of equal scores."
        self.assertEqual(self.grounded(passage), CONTEXT_PREFIX + passage)

    def test_accepts_a_complete_line_from_a_multiline_prompt(self):
        passage = "rank must retain equal-score order."
        prompt = "Update the ranking module.\n\n  " + passage + "\n\nDo not touch output formatting."
        self.assertEqual(self.grounded(passage, prompt), CONTEXT_PREFIX + passage)

    def test_rejects_markdown_block_quotes(self):
        self.unsupported(
            "rank should reverse equal-score order.",
            "> rank should reverse equal-score order.",
        )

    def test_rejects_fenced_code_examples(self):
        passage = "rank should reverse equal-score order."
        self.unsupported(passage, "Example to discuss:\n```text\n" + passage + "\n```")
        self.unsupported(passage, "~~~text\n" + passage + "\n~~~")

    def test_does_not_silently_truncate_long_requirements(self):
        self.unsupported("rank " + "x" * MAX_EXCERPT_LENGTH)

    def test_maximum_passage_fits_patch_limit(self):
        passage = "rank " + "x" * (MAX_EXCERPT_LENGTH - len("rank "))
        self.assertEqual(len(self.grounded(passage)), 240)

    def test_rejects_missing_or_non_text_evidence(self):
        for prompt, decision in ((None, {"comment": "rank"}), ("rank", {}), ("rank", {"comment": 3})):
            with self.subTest(prompt=prompt, decision=decision):
                with self.assertRaises(WorkerError):
                    build_grounded_comment(decision, prompt, "rank")


if __name__ == "__main__":
    unittest.main()

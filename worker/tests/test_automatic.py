"""Observed transactions use model synthesis while edits preserve parsed code."""

import io
import json
import unittest
from unittest.mock import patch

from btl_agent.__main__ import serve
from btl_agent.agent import Agent
from btl_agent.automatic import annotate_observed, retire_annotation
from btl_agent.parsing import discover, fingerprint
from btl_agent.patches import apply_suggestions
from btl_agent.types import WorkerError, digest


BEFORE = "def rank(items):\n    return items\n"
AFTER = "def rank(items):\n    return sorted(items, key=lambda item: item[1])\n"
QUOTE = "Preserve the input order of equal scores."
EXPLANATION = "Sort by score without reordering equal-score items."
COMMENT = "# " + EXPLANATION


def params(source=AFTER, before=BEFORE, prompt=QUOTE, **extra):
    return {"uri": "file:///workspace/rank.py", "language": "python", "source": source,
            "previousSource": before, "maxSuggestions": 3,
            "intent": {"prompt": prompt, "source": "codex", "eventId": "event-one",
                       "sessionId": "session-one", "turnId": "turn-one"}, **extra}


def evidence(prompt, quote=None):
    quote = prompt if quote is None else quote
    offset = prompt.index(quote)
    start = len(prompt[:offset].encode("utf-16-le")) // 2
    return [{"quote": quote, "start": start, "end": start + len(quote.encode("utf-16-le")) // 2,
             "promptHash": digest(prompt)}]


def known(before=BEFORE, **extra):
    declaration = discover(before, "python")[0]
    return {"id": "original-requirement", "symbolId": declaration.symbol_id,
            "symbolName": declaration.name, "quote": QUOTE, "prompt": QUOTE,
            "promptHash": digest(QUOTE), "commentText": COMMENT, "commentKind": "synthesized",
            "evidence": evidence(QUOTE), "generation": {"provider": "local", "model": "test.gguf"},
            "codeHash": declaration.code_hash, "status": "active", "scopeStatus": "changed", **extra}


class NoInference:
    def generate(self, *args):
        raise AssertionError("Unexpected direct inference; synthesis is controlled in these tests")

    def unload(self):
        pass


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.backend = NoInference()
        self.calls = []
        self.answer = EXPLANATION
        self.failure = None
        self.synthesis = patch("btl_agent.automatic.synthesize_comment", side_effect=self.generate).start()
        self.addCleanup(patch.stopall)

    def generate(self, backend, request, declaration, metrics, verify=True):
        self.calls.append((backend, request, declaration, verify))
        metrics["modelCalls"] += 2
        if self.failure:
            raise self.failure
        return {"comment": self.answer, "evidence": evidence(request["intent"]["prompt"]),
                "generation": {"provider": "local", "model": "test.gguf"}, "reason": "Explain the ranking contract."}

    def annotate(self, request):
        return annotate_observed(request, self.backend)

    def test_changed_block_and_full_captured_prompt_reach_model_synthesis(self):
        result = Agent(self.backend).annotate_observed(params())
        self.assertEqual(result["annotatedSource"], COMMENT + "\n" + AFTER)
        self.assertNotIn("User context:", result["annotatedSource"])
        self.assertEqual(self.calls[0][0], self.backend)
        self.assertEqual(self.calls[0][1]["intent"]["prompt"], QUOTE)
        self.assertEqual(self.calls[0][2].source, AFTER)
        self.assertTrue(self.calls[0][3], "Automatic output requires the separate support judgment")
        self.assertEqual(result["metrics"]["modelCalls"], 2)
        record = result["requirements"][0]
        self.assertEqual(record["commentKind"], "synthesized")
        self.assertEqual(record["quote"], QUOTE)
        self.assertNotEqual(record["commentText"], "# " + QUOTE)
        self.assertEqual(record["generation"]["model"], "test.gguf")
        self.assertEqual(record["promptHash"], digest(QUOTE))
        self.assertEqual(fingerprint(AFTER, "python"), fingerprint(result["annotatedSource"], "python"))

    def test_evidence_does_not_need_to_name_the_function(self):
        result = self.annotate(params(prompt="Keep ties stable for cached rankings."))
        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(result["requirements"][0]["quote"], "Keep ties stable for cached rankings.")

    def test_full_conditions_and_negation_remain_separate_original_evidence(self):
        prompt = "Never change tie order\nunless reverse=True was explicitly requested."
        result = self.annotate(params(prompt=prompt))
        self.assertEqual(result["requirements"][0]["evidence"][0]["quote"], prompt)
        self.assertEqual(self.calls[0][1]["intent"]["prompt"], prompt)

    def test_model_abstention_or_mismatch_keeps_source_unchanged(self):
        self.failure = WorkerError("synthesis_abstained", "The new task conflicts with an active original requirement.")
        result = self.annotate(params())
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["annotatedSource"], AFTER)
        self.assertIn("conflicts", result["skipped"][0]["reason"])

    def test_unconfigured_generation_does_not_fall_back_to_quotations(self):
        with self.assertRaises(WorkerError) as error:
            annotate_observed(params())
        self.assertEqual(error.exception.code, "model_unavailable")

    def test_actionable_provider_failures_are_retryable_request_errors(self):
        for code in ("runtime_unavailable", "provider_auth", "provider_rate_limit", "provider_timeout"):
            self.failure = WorkerError(code, "Provider needs attention.")
            with self.subTest(code=code), self.assertRaises(WorkerError) as error:
                self.annotate(params())
            self.assertEqual(error.exception.code, code)

    def test_fabricated_evidence_or_unsafe_comment_is_not_applied(self):
        for answer in ("first\nsecond", "noqa: F401", r"escaped \u000a code"):
            with self.subTest(answer=answer):
                self.answer = answer
                result = self.annotate(params())
                self.assertEqual(result["annotatedSource"], AFTER)
        self.synthesis.side_effect = lambda *args, **kwargs: {
            "comment": EXPLANATION, "evidence": evidence("Unrelated text."),
            "generation": {"provider": "local", "model": "test.gguf"}}
        self.assertEqual(self.annotate(params())["annotatedSource"], AFTER)

    def test_human_comments_and_docstrings_are_untouched_without_model_calls(self):
        for source in ("# Human explanation.\n" + AFTER,
                       'def rank(items):\n    """Human documentation."""\n    return sorted(items)\n'):
            with self.subTest(source=source):
                self.assertEqual(self.annotate(params(source))["annotatedSource"], source)
        self.assertEqual(self.calls, [])

    def test_missing_provenance_or_before_snapshot_cannot_generate(self):
        for field in ("eventId", "sessionId", "turnId"):
            request = params()
            del request["intent"][field]
            with self.assertRaises(WorkerError):
                self.annotate(request)
        request = params()
        del request["previousSource"]
        with self.assertRaises(WorkerError):
            self.annotate(request)

    def test_duplicate_scopes_are_not_assigned_by_guessing(self):
        before = "class Cache:\n    def rank(self, items):\n        return items\n    def rank(self, items):\n        return items\n"
        source = before.replace("return items", "return sorted(items)")
        self.assertEqual(self.annotate(params(source, before))["suggestions"], [])
        self.assertEqual(self.calls, [])

    def test_original_contexts_survive_generated_wording_updates(self):
        previous = COMMENT + "\n" + BEFORE
        source = COMMENT + "\n" + AFTER
        old = known()
        self.answer = "Rank entries stably so cached equal-score results retain their order."
        result = self.annotate(params(source, previous, "Optimize the ranking implementation.", knownRequirements=[old]))
        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(self.calls[0][1]["knownRequirements"], [old])
        self.assertEqual(result["requirements"][0]["supersedes"], [])
        self.assertEqual(old["quote"], QUOTE)
        self.assertEqual(result["requirements"][0]["quote"], "Optimize the ranking implementation.")

    def test_missing_scope_original_reaches_model_without_ownership_rebinding(self):
        old = known(symbolId="python:removed_name", symbolName="removed_name", scopeStatus="missing")
        result = self.annotate(params(knownRequirements=[old]))
        self.assertEqual(self.calls[0][1]["knownRequirements"], [old])
        self.assertEqual(result["requirements"][0]["symbolId"], "python:rank")
        self.assertEqual(old["symbolId"], "python:removed_name")

    def test_explicit_supersession_is_carried_to_history_without_deleting_old_data(self):
        self.answer = "Reverse equal-score entries under the revised ranking contract."
        request = params(COMMENT + "\n" + AFTER, COMMENT + "\n" + BEFORE,
                         "Supersede original-requirement\nReverse the tie order.", knownRequirements=[known()])
        result = self.annotate(request)
        self.assertEqual(result["requirements"][0]["supersedes"], ["original-requirement"])

    def test_removed_annotation_is_restored_only_for_unchanged_code(self):
        result = self.annotate(params(BEFORE, COMMENT + "\n" + BEFORE, "Clean formatting.", knownRequirements=[known()]))
        self.assertEqual(result["annotatedSource"], COMMENT + "\n" + BEFORE)
        self.assertEqual(result["requirements"], [])
        self.assertEqual(self.calls, [])

    def test_changed_body_requires_fresh_generation_instead_of_stale_restore(self):
        self.failure = WorkerError("synthesis_abstained", "Not enough current evidence.")
        result = self.annotate(params(before=COMMENT + "\n" + BEFORE, prompt="Optimize ranking.", knownRequirements=[known()]))
        self.assertEqual(result["annotatedSource"], AFTER)
        self.assertEqual(len(self.calls), 1)

    def test_manual_removal_before_agent_edit_is_respected(self):
        result = self.annotate(params(knownRequirements=[known()]))
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(self.calls, [])

    def test_missing_original_evidence_cannot_restore_historical_output(self):
        record = known()
        del record["prompt"]
        result = self.annotate(params(BEFORE, COMMENT + "\n" + BEFORE, "Clean formatting.", knownRequirements=[record]))
        self.assertEqual(result["suggestions"], [])

    def test_repeated_unchanged_source_does_not_call_model_or_duplicate(self):
        source = COMMENT + "\n" + AFTER
        for before in (source, source + "\n"):
            self.assertEqual(self.annotate(params(source, before, knownRequirements=[known()]))["suggestions"], [])
        self.assertEqual(self.calls, [])

    def test_java_synthesis_preserves_every_non_comment_token(self):
        before = "class Example {\n    int rank(int x) { return x; }\n}\n"
        source = before.replace("return x;", "return x + 1;")
        result = self.annotate(params(source, before, language="java"))
        self.assertIn("// " + EXPLANATION, result["annotatedSource"])
        self.assertEqual(fingerprint(source, "java"), fingerprint(result["annotatedSource"], "java"))

    def test_inference_budget_counts_abstentions_not_only_successes(self):
        before = "".join(f"def f{i}(x):\n    return x\n" for i in range(5))
        source = before.replace("return x", "return x + 1")
        self.failure = WorkerError("synthesis_abstained", "Unclear context.")
        result = self.annotate(params(source, before, maxSuggestions=3))
        self.assertEqual(result["metrics"]["modelCalls"], 6)
        self.assertEqual(sum("budget" in item["reason"] for item in result["skipped"]), 2)

    def test_batch_application_rejects_overlapping_or_stale_edits(self):
        proposal = self.annotate(params())["suggestions"][0]
        with self.assertRaises(WorkerError):
            apply_suggestions(AFTER, "python", [proposal, proposal])
        with self.assertRaises(WorkerError):
            apply_suggestions(AFTER, "python", [{**proposal, "oldText": "different"}])

    def test_utf16_evidence_offsets_and_source_crlf_are_preserved(self):
        before = '@cache\r\ndef café(x):\r\n    return "😀" + x\r\n'
        source = before.replace('"😀"', '"😀😀"')
        prompt = "😀\nRetain every emoji."
        spans = evidence(prompt, "Retain every emoji.")
        self.synthesis.side_effect = lambda *args, **kwargs: {
            "comment": "Retain input emoji while combining the prefix.",
            "evidence": spans, "generation": {"provider": "local", "model": "test.gguf"}}
        result = self.annotate(params(source, before, prompt))
        self.assertEqual(result["requirements"][0]["quoteStart"], 3)
        self.assertIn("\r\n@cache", result["annotatedSource"])
        self.assertEqual(fingerprint(source, "python"), fingerprint(result["annotatedSource"], "python"))

    def test_retirement_removes_only_the_exact_synthesized_annotation(self):
        source = COMMENT + "\n" + AFTER
        result = retire_annotation({"uri": "file:///rank.py", "language": "python", "source": source,
                                    "requirement": known()})
        self.assertEqual(result["annotatedSource"], AFTER)
        self.assertEqual(fingerprint(source, "python"), fingerprint(result["annotatedSource"], "python"))

    def test_retirement_preserves_human_edits_or_adjacent_documentation(self):
        for source in (COMMENT.replace("Sort", "Carefully sort") + "\n" + AFTER,
                       "# Human documentation.\n" + COMMENT + "\n" + AFTER, AFTER):
            result = retire_annotation({"uri": "file:///rank.py", "language": "python", "source": source,
                                        "requirement": known()})
            self.assertEqual(result["annotatedSource"], source)

    def test_legacy_quotation_retirement_remains_supported(self):
        quote = "rank must preserve tie order."
        comment = "# User context: " + quote
        record = known(commentKind="quotation", quote=quote, prompt=quote, commentText=comment)
        request = {"id": "retire", "method": "retireAnnotation",
                   "params": {"uri": "file:///rank.py", "language": "python",
                              "source": comment + "\n" + AFTER, "requirement": record}}
        output = io.StringIO()
        serve(io.BytesIO((json.dumps(request) + "\n").encode()), output, NoInference())
        self.assertEqual(json.loads(output.getvalue())["result"]["annotatedSource"], AFTER)


if __name__ == "__main__":
    unittest.main()

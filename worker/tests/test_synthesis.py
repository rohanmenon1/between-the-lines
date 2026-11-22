"""Generated wording is separate from exact original evidence and code edits."""

from types import SimpleNamespace
import unittest

from btl_agent.agent import Agent
from btl_agent.backend import decision_schema
from btl_agent.grounding import prompt_evidence
from btl_agent.parsing import discover, fingerprint
from btl_agent.patches import apply_suggestions
from btl_agent.synthesis import generation_payload, synthesize_comment
from btl_agent.types import WorkerError, digest


SOURCE = 'def rank(items):\n    return sorted(items, key=lambda item: item["score"], reverse=True)\n'
PROMPT = "Keep tied scores in their original order so the leaderboard does not reshuffle between refreshes."
COMMENT = "Keep tied scores stable so refreshes do not reshuffle the leaderboard."


def request(**extra):
    return {"uri": "file:///workspace/rank.py", "language": "python", "source": SOURCE,
            "intent": {"source": "manual", "prompt": PROMPT}, **extra}


def decision(comment=COMMENT, quote=PROMPT):
    return {"action": "synthesize_comment", "comment": comment,
            "promptEvidence": {"quote": quote}, "reason": "Stable sorting retains the required tie order."}


class Model:
    def __init__(self, *responses, provider="local"):
        self.responses, self.calls = list(responses), []
        self.configuration = SimpleNamespace(provider=provider, model="chosen-model", model_path="baseline.gguf")

    def generate(self, system, payload):
        self.calls.append((system, payload))
        if not self.responses:
            raise AssertionError("Unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response, {"inputTokens": 12, "outputTokens": 8}

    def identity(self):
        return "test-synthesis-model"

    def unload(self):
        pass


def generate(model, params=None, verify=True):
    params = params or request()
    metrics = {"modelCalls": 0, "inputTokens": 0, "outputTokens": 0}
    result = synthesize_comment(model, params, discover(params["source"], params["language"])[0], metrics, verify)
    return result, metrics


class SynthesisTests(unittest.TestCase):
    def test_support_verdict_precedes_bounded_explanation(self):
        schema = decision_schema([], "comment_verification")
        self.assertEqual(list(schema["properties"]), ["supported", "reason"])
        self.assertEqual(schema["required"], ["supported", "reason"])
        self.assertLessEqual(schema["properties"]["reason"]["maxLength"], 300)

    def test_copying_the_prompt_is_not_a_generated_explanation(self):
        model = Model(decision(comment=PROMPT))
        with self.assertRaises(WorkerError) as error:
            generate(model)
        self.assertEqual(error.exception.code, "synthesis_abstained")
        self.assertEqual(len(model.calls), 1)

    def test_unnamed_task_generates_readable_wording_with_separate_evidence(self):
        self.assertNotIn("rank", PROMPT)
        model = Model(decision(), {"supported": True, "reason": "Supported purpose and tie behavior."})
        result, metrics = generate(model)
        self.assertEqual(result["comment"], COMMENT)
        self.assertNotEqual(result["comment"], result["quote"])
        self.assertEqual(result["evidence"][0]["quote"], PROMPT)
        self.assertEqual(result["promptHash"], digest(PROMPT))
        self.assertEqual(metrics, {"modelCalls": 2, "inputTokens": 24, "outputTokens": 16})
        self.assertEqual(model.calls[0][1]["currentSource"], SOURCE)
        self.assertEqual(model.calls[0][1]["intent"]["prompt"], PROMPT)
        self.assertEqual([call[1]["task"] for call in model.calls], ["comment_synthesis", "comment_verification"])
        self.assertEqual(result["verification"]["kind"], "model_judgment")

    def test_routing_identifiers_do_not_change_semantic_payload_or_cache(self):
        first = request(knownRequirements=[{"id": "requirement-one", "symbolId": "python:rank",
                        "prompt": PROMPT, "quote": PROMPT, "source": "codex",
                        "eventId": "old-event-a", "sessionId": "old-session-a", "turnId": "old-turn-a"}])
        first["intent"].update(source="codex", eventId="event-a", sessionId="session-a", turnId="turn-a")
        second = request(knownRequirements=[{**first["knownRequirements"][0], "source": "copilot",
                         "eventId": "old-event-b", "sessionId": "old-session-b", "turnId": "old-turn-b"}])
        second["intent"].update(source="copilot", eventId="event-b", sessionId="session-b", turnId="turn-b")
        declaration = discover(SOURCE, "python")[0]
        self.assertEqual(generation_payload(first, declaration), generation_payload(second, declaration))
        self.assertEqual(generation_payload(first, declaration)["intent"], {"prompt": PROMPT})
        agent = Agent(Model(decision()))
        self.assertEqual(agent._key(first, declaration), agent._key(second, declaration))

    def test_history_is_scoped_to_target_and_actual_parsed_ancestors(self):
        source = ('class Board:\n'
                  '    def rank(self, items):\n        return sorted(items)\n'
                  '    def bill(self, amount):\n        return amount * 2\n'
                  'def unrelated():\n    return 1\n')
        records = [
            {"id": "class-rule", "symbolId": "python:Board", "prompt": "Keep board operations repeatable."},
            {"id": "rank-rule", "symbolId": "python:Board.rank", "prompt": "Preserve tie order."},
            {"id": "bill-rule", "symbolId": "python:Board.bill", "prompt": "Invoice each account once."},
            {"id": "other-rule", "symbolId": "python:unrelated", "prompt": "Return one."},
            {"id": "missing-rule", "symbolId": "python:old_rank", "scopeStatus": "missing", "prompt": "The earlier scope is gone."},
        ]
        params = request(source=source, knownRequirements=records)
        declaration = next(item for item in discover(source, "python") if item.name == "rank")
        model = Model(decision(), {"supported": True, "reason": "Respects the scoped originals."})
        metrics = {"modelCalls": 0, "inputTokens": 0, "outputTokens": 0}
        synthesize_comment(model, params, declaration, metrics)
        for _, payload in model.calls:
            self.assertEqual([item["requirementId"] for item in payload["originalContexts"]],
                             ["class-rule", "rank-rule", "missing-rule"])
            self.assertEqual(payload["originalContexts"][-1]["scopeStatus"], "missing")
        changed = request(source=source, knownRequirements=[
            {**record, "prompt": "An unrelated billing instruction changed."} if record["id"] == "bill-rule" else record
            for record in records])
        agent = Agent(model)
        self.assertEqual(agent._key(params, declaration), agent._key(changed, declaration))

    def test_essential_or_unbound_originals_over_budget_abstain_before_inference(self):
        for scope in ("python:rank", "python:removed_scope"):
            params = request(knownRequirements=[{"id": "large-original", "symbolId": scope,
                             "prompt": "Original requirement " + "x" * 9000, "quote": "Keep the original."}])
            model = Model()
            with self.subTest(scope=scope), self.assertRaises(WorkerError) as error:
                generate(model, params)
            self.assertEqual(error.exception.code, "context_limit")
            self.assertEqual(model.calls, [])
            result = Agent(model).analyze(params)
            self.assertEqual(result["suggestions"], [])
            self.assertIn("context_limit", result["skipped"][0]["reason"])
            self.assertEqual(result["metrics"]["modelCalls"], 0)

    def test_manual_analysis_uses_one_generation_and_preserves_whole_source(self):
        model = Model(decision())
        result = Agent(model).analyze(request())
        self.assertEqual(result["metrics"]["modelCalls"], 1)
        self.assertEqual(len(result["suggestions"]), 1)
        suggestion = result["suggestions"][0]
        self.assertEqual(suggestion["commentText"], "# " + COMMENT)
        self.assertEqual(suggestion["commentKind"], "synthesized")
        self.assertEqual(suggestion["promptEvidence"][0]["quote"], PROMPT)
        patched = apply_suggestions(SOURCE, "python", result["suggestions"])
        self.assertEqual(fingerprint(SOURCE, "python"), fingerprint(patched, "python"))

    def test_both_calls_receive_every_active_original_without_generated_prose(self):
        original = "Do not reorder tied scores."
        records = [{"id": "old-one", "status": "active", "prompt": original,
                    "quote": original, "commentText": "# This generated wording is not original intent.",
                    "symbolId": "python:rank", "sessionId": "old-session", "turnId": "old-turn"},
                   {"id": "retired", "status": "retired", "prompt": "No longer active."}]
        model = Model(decision(), {"supported": True, "reason": "Respects the old constraint."})
        generate(model, request(knownRequirements=records))
        for _, payload in model.calls:
            contexts = payload["originalContexts"]
            self.assertEqual(len(contexts), 1)
            self.assertEqual(contexts[0]["originalPrompt"], original)
            self.assertEqual(contexts[0]["originalEvidence"][0]["quote"], original)
            self.assertNotIn("commentText", contexts[0])
            self.assertFalse(contexts[0]["explicitlySupersededByCurrentPrompt"])

    def test_supersession_is_explicit_and_history_is_not_current_evidence(self):
        params = request(knownRequirements=[{"id": "old-one", "prompt": "The original constraint.", "quote": "The original constraint."}])
        params["intent"]["prompt"] = "Supersede old-one\n" + PROMPT
        model = Model(decision(), {"supported": True, "reason": "Explicit replacement."})
        result, _ = generate(model, params)
        self.assertTrue(model.calls[0][1]["originalContexts"][0]["explicitlySupersededByCurrentPrompt"])
        self.assertEqual(result["quote"], PROMPT)
        self.assertEqual(result["promptHash"], digest(params["intent"]["prompt"]))
        wrong = Model(decision(quote="The original constraint."))
        with self.assertRaises(WorkerError) as error:
            generate(wrong, params)
        self.assertEqual(error.exception.code, "unsupported_evidence")
        self.assertEqual(len(wrong.calls), 1)

    def test_verifier_rejection_and_invalid_verdict_never_copy_the_prompt(self):
        for verdict in ({"supported": False, "reason": "The code does not preserve ties."},
                        {"supported": "true", "reason": "Not a boolean."}, {"supported": True}):
            with self.subTest(verdict=verdict), self.assertRaises(WorkerError):
                generate(Model(decision(), verdict))

    def test_abstention_and_provider_failures_have_no_fallback(self):
        for action in ("skip", "flag_mismatch"):
            model = Model({"action": action, "reason": "Insufficient support."})
            with self.subTest(action=action), self.assertRaises(WorkerError) as error:
                generate(model)
            self.assertEqual(error.exception.code, "synthesis_abstained")
            self.assertEqual(len(model.calls), 1)
        model = Model(WorkerError("provider_auth", "Credentials rejected."), provider="custom")
        with self.assertRaises(WorkerError) as error:
            generate(model)
        self.assertEqual(error.exception.code, "provider_auth")
        self.assertEqual(len(model.calls), 1)

    def test_fabricated_or_clipped_evidence_is_rejected_before_verification(self):
        for quote in ("Keep ties stable.", "Keep tied scores in their original order", "rank is stable."):
            model = Model(decision(quote=quote))
            with self.subTest(quote=quote), self.assertRaises(WorkerError) as error:
                generate(model)
            self.assertEqual(error.exception.code, "unsupported_evidence")
            self.assertEqual(len(model.calls), 1)

    def test_unsafe_comments_do_not_reach_verification(self):
        for comment in ("# Add a marker.", "first\nsecond", "User context: " + PROMPT,
                        "escape \\u000a", "hidden\u0000value", "x" * 241):
            model = Model(decision(comment=comment))
            with self.subTest(comment=comment), self.assertRaises(WorkerError):
                generate(model)
            self.assertEqual(len(model.calls), 1)

    def test_grammar_truncated_fragments_are_rejected_before_review(self):
        for comment in ("Keep cached falsey values such as `0", "Preserve original tie ord",
                        "Keep values from `dict.fromkeys."):
            model = Model(decision(comment=comment))
            with self.subTest(comment=comment), self.assertRaises(WorkerError) as error:
                generate(model)
            self.assertEqual(error.exception.code, "unsafe_proposal")
            self.assertEqual(len(model.calls), 1)

    def test_multiline_crlf_evidence_retains_complete_original_and_utf16_offsets(self):
        quote = "Keep the order of tied scores\nwhen the display refreshes."
        prompt = "😀 Task context\r\n" + quote.replace("\n", "\r\n") + "\r\n"
        evidence = prompt_evidence(quote, prompt)
        self.assertEqual(evidence["quote"], quote.replace("\n", "\r\n"))
        encoded = prompt.encode("utf-16-le")
        self.assertEqual(encoded[evidence["start"] * 2:evidence["end"] * 2].decode("utf-16-le"), evidence["quote"])

    def test_generation_and_review_schemas_are_distinct(self):
        generation = decision_schema([], "comment_synthesis")
        actions = {item["properties"]["action"]["const"]: item for item in generation["oneOf"]}
        self.assertIn("synthesize_comment", actions)
        self.assertNotIn("propose_comment", actions)
        self.assertEqual(set(actions["synthesize_comment"]["required"]), {"action", "reason", "comment", "promptEvidence"})
        review = decision_schema([], "comment_verification")
        self.assertEqual(set(review["required"]), {"supported", "reason"})
        self.assertEqual(review["properties"]["supported"]["type"], "boolean")

    def test_exact_evidence_id_selection_keeps_generated_prose_separate(self):
        selected = decision()
        selected["promptEvidence"] = {"passageId": "p0"}
        result, _ = generate(Model(selected, {"supported": True, "reason": "Supported."}))
        self.assertEqual(result["comment"], COMMENT)
        self.assertEqual(result["quote"], PROMPT)
        bad = decision()
        bad["promptEvidence"] = {"passageId": "not-an-original-passage"}
        with self.assertRaises(WorkerError):
            generate(Model(bad))
        schema = decision_schema([], "comment_synthesis", ["p0", "p1"])
        synth = next(item for item in schema["oneOf"] if item["properties"]["action"]["const"] == "synthesize_comment")
        self.assertEqual(synth["properties"]["promptEvidence"]["properties"]["passageId"]["enum"], ["p0", "p1"])

    def test_evidence_cannot_clip_a_following_condition(self):
        prompt = "Retry network errors\nonly when the request is idempotent."
        with self.assertRaises(WorkerError):
            prompt_evidence("Retry network errors", prompt)
        self.assertEqual(prompt_evidence(prompt, prompt)["quote"], prompt)

    def test_native_synthesis_grammar_compiles_without_large_repetition_expansion(self):
        try:
            from llama_cpp import LlamaGrammar
        except ImportError:
            self.skipTest("Native inference runtime is not installed.")
        import json
        for task in ("comment_synthesis", "comment_verification"):
            with self.subTest(task=task):
                grammar = LlamaGrammar.from_json_schema(json.dumps(decision_schema([], task)), verbose=False)
                self.assertIsNotNone(grammar)
        with self.assertRaises(WorkerError):
            prompt_evidence("x" * 2001, "x" * 2001)

    def test_generation_metadata_names_only_the_selected_provider_and_model(self):
        result, _ = generate(Model(decision(), provider="custom"), verify=False)
        self.assertEqual(result["generation"], {"provider": "custom", "model": "chosen-model"})


if __name__ == "__main__":
    unittest.main()

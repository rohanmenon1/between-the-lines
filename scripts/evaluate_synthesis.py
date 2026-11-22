"""Record local synthesis outputs for human review; never score meaning by a model."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

from benchmark_worker import hardware, process_usage
from btl_agent.backend import LlamaBackend
from btl_agent.parsing import discover, fingerprint
from btl_agent.patches import make_suggestion, apply_suggestions
from btl_agent.synthesis import synthesize_comment
from btl_agent.types import Configuration, WorkerError


class AuditedBackend(LlamaBackend):
    def __init__(self):
        super().__init__()
        self.raw_calls = []
        self._audited = None

    def _load(self):
        super()._load()
        if self._audited is self._model:
            return
        self._audited = self._model
        completion = self._model.create_completion

        def record(**kwargs):
            started = time.perf_counter()
            result = completion(**kwargs)
            self.raw_calls.append({"text": result.get("choices", [{}])[0].get("text"),
                                   "usage": result.get("usage"),
                                   "elapsedMs": round((time.perf_counter() - started) * 1000)})
            return result
        self._model.create_completion = record


def usage():
    handle = ctypes.windll.kernel32.GetCurrentProcess() if os.name == "nt" else None
    rss, cpu, peak = process_usage(SimpleNamespace(pid=os.getpid(), _handle=handle))
    return {"rssBytes": rss, "processCpuSeconds": cpu, "peakRssBytes": peak}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "eval" / "synthesis-cases.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id", action="append", help="Run only these case IDs; may be repeated.")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--context-tokens", type=int, default=2048)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output path so earlier results remain available.")
    model = args.model_path.resolve(strict=True)
    data = json.loads(args.cases.read_text(encoding="utf-8-sig"))
    cases = data["cases"] if isinstance(data, dict) else data
    if args.case_id:
        missing = set(args.case_id) - {case["id"] for case in cases}
        if missing:
            parser.error("Unknown case IDs: " + ", ".join(sorted(missing)))
        cases = [case for case in cases if case["id"] in args.case_id]
    cases = cases[:args.limit] if args.limit else cases
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        parser.error("Provide between one and 100 cases.")
    backend = AuditedBackend()
    backend.configure(Configuration.from_params({"modelPath": str(model), "threads": args.threads,
                      "contextTokens": args.context_tokens, "maxOutputTokens": args.max_output_tokens,
                      "idleSeconds": 60}))
    with model.open("rb") as stream:
        model_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"label": "Small local development evaluation; human quality review required",
              "model": {"path": str(model), "bytes": model.stat().st_size, "sha256": model_hash},
              "hardware": hardware(), "settings": {"threads": args.threads, "contextTokens": args.context_tokens,
              "maxOutputTokens": args.max_output_tokens, "supportReview": "same-model judgment, not proof"},
              "caseSource": str(args.cases.resolve()), "cases": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for case in cases:
            started, cpu_started = time.perf_counter(), time.process_time()
            metrics = {"modelCalls": 0, "inputTokens": 0, "outputTokens": 0}
            item = {key: value for key, value in case.items() if key not in {"result", "metrics", "rawCalls", "status", "error"}}
            first = len(backend.raw_calls)
            try:
                params = {"language": case["language"], "source": case["source"],
                          "intent": {"source": "manual", "prompt": case["prompt"]},
                          "knownRequirements": case.get("knownRequirements", [])}
                declarations = discover(case["source"], case["language"])
                target = next((value for value in declarations if value.name == case.get("symbol")), None)
                target = target or next((value for value in declarations if value.kind != "class"), declarations[0])
                generated = synthesize_comment(backend, params, target, metrics, verify=True)
                suggestion = make_suggestion(case["source"], case["language"], target,
                                             generated["comment"], generated["reason"], [], [])
                patched = apply_suggestions(case["source"], case["language"], [suggestion])
                item.update(status="accepted_by_model", result=generated,
                            syntaxPreserved=fingerprint(case["source"], case["language"]) == fingerprint(patched, case["language"]))
            except WorkerError as error:
                item.update(status=error.code, error=str(error))
            item["metrics"] = {**metrics, "elapsedMs": round((time.perf_counter() - started) * 1000),
                               "cpuSeconds": round(time.process_time() - cpu_started, 3), **usage()}
            item["rawCalls"] = backend.raw_calls[first:]
            report["cases"].append(item)
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({"id": item["id"], "status": item["status"],
                              "comment": item.get("result", {}).get("comment"), "metrics": item["metrics"]}, ensure_ascii=False), flush=True)
    finally:
        report["beforeUnload"] = usage()
        backend.unload()
        report["afterUnload"] = usage()
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

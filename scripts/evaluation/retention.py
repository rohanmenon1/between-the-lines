"""Prepare blinded later-edit jobs and grade original intent separately from new work."""

import ast
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import uuid


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def load_retention_cases(path):
    raw = Path(path).read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("Retention fixtures exceed 1 MB.")
    data = json.loads(raw)
    if data.get("schemaVersion") != 1 or not isinstance(data.get("cases"), list) or not 1 <= len(data["cases"]) <= 100:
        raise ValueError("Provide version 1 retention fixtures with one to 100 cases.")
    seen = set()
    for case in data["cases"]:
        identifier = case.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9_-]{1,80}", identifier) or identifier in seen:
            raise ValueError("Retention cases need unique simple IDs.")
        seen.add(identifier)
        if case.get("language") != "python":
            raise ValueError("The executable retention fixtures currently support Python only.")
        for field in ("source", "originalPrompt", "followUpTask", "symbol"):
            if not isinstance(case.get(field), str) or not case[field].strip() or len(case[field]) > 131072:
                raise ValueError(f"Case {identifier}: missing or excessive {field}.")
        functions = [node for node in ast.parse(case["source"]).body if isinstance(node, ast.FunctionDef)]
        if sum(node.name == case["symbol"] for node in functions) != 1:
            raise ValueError(f"Case {identifier}: choose one top-level function.")
        requirements = case.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError(f"Case {identifier}: record original requirements.")
        ids = [requirement.get("id") for requirement in requirements]
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError(f"Case {identifier}: requirement IDs must be unique.")
        for field in ("checks", "followUpChecks"):
            checks = case.get(field)
            if not isinstance(checks, list) or not 1 <= len(checks) <= 100:
                raise ValueError(f"Case {identifier}: provide bounded {field}.")
            for check in checks:
                if (not isinstance(check, dict) or not isinstance(check.get("function"), str)
                        or not check["function"].isidentifier() or not isinstance(check.get("args", []), list)
                        or not isinstance(check.get("kwargs", {}), dict) or "expected" not in check
                        or type(check.get("inputsUnchanged", False)) is not bool):
                    raise ValueError(f"Case {identifier}: invalid behavioral check.")
                if field == "checks" and check.get("requirementId") not in ids:
                    raise ValueError(f"Case {identifier}: a check references an unknown requirement.")
        if {check["requirementId"] for check in case["checks"]} != set(ids):
            raise ValueError(f"Case {identifier}: every original requirement needs a check.")
    return data, _digest(raw)


def _with_comment(case, comment):
    if comment is None:
        return case["source"]
    from btl_agent.types import validate_comment_text

    line = validate_comment_text("# " + comment, "python")
    tree = ast.parse(case["source"])
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == case["symbol"])
    row = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list]) - 1
    lines = case["source"].splitlines(keepends=True)
    lines.insert(row, line + "\n")
    annotated = "".join(lines)
    if ast.dump(tree) != ast.dump(ast.parse(annotated)):
        raise ValueError("An annotation changed executable source.")
    return annotated


def export_retention_tasks(tasks_path, output_dir, annotations_path=None, seed=42):
    """Give each later coding session only the new task and its source condition."""
    tasks, tasks_hash = load_retention_cases(tasks_path)
    output = Path(output_dir)
    if output.exists():
        raise ValueError("Choose a new retention directory; existing jobs are never overwritten.")
    annotation_cases = [{"id": case["id"], "language": "python", "symbol": case["symbol"],
                         "prompt": case["originalPrompt"], "source": case["source"],
                         "expected": "comment", "family": case["id"],
                         "reviewNotes": "Explain only the original requirement, not a future task.",
                         "provenance": {"kind": "synthetic", "humanReviewed": False}}
                        for case in tasks["cases"]]
    results = {}
    variants = ["no_comment"]
    annotations_hash = None
    if annotations_path is not None:
        raw = Path(annotations_path).read_bytes()
        annotations_hash = _digest(raw)
        report = json.loads(raw)
        if report.get("schemaVersion") != 1 or report.get("status") != "completed":
            raise ValueError("Supply a completed annotation comparison report.")
        frozen = {case["id"]: case for case in report["dataset"]["cases"]}
        if set(frozen) != {case["id"] for case in annotation_cases}:
            raise ValueError("The annotation report must cover exactly these retention cases.")
        for case in annotation_cases:
            if any(frozen[case["id"]].get(field) != case[field] for field in ("prompt", "source", "symbol")):
                raise ValueError("The annotation report uses different original source or intent.")
        declared = report["settings"]["variants"]
        if (not isinstance(declared, list) or not declared
                or any(not isinstance(variant, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", variant) for variant in declared)):
            raise ValueError("Annotation variants need nonempty simple names.")
        variants += declared
        if len(set(variants)) != len(variants):
            raise ValueError("Annotation variants must be unique and cannot be named no_comment.")
        for result in report["results"]:
            if (result.get("status") not in {"comment", "abstained", "error"}
                    or result["status"] == "comment" and (not isinstance(result.get("comment"), str) or not result["comment"].strip())
                    or result["status"] != "comment" and result.get("comment") is not None):
                raise ValueError("Annotation results must distinguish comments, abstentions and errors explicitly.")
            identity = result["caseId"], result["variant"]
            if identity in results or identity[0] not in frozen or identity[1] not in variants:
                raise ValueError("Unexpected or duplicate annotation result.")
            results[identity] = result
        if set(results) != {(case["id"], variant) for case in annotation_cases for variant in variants[1:]}:
            raise ValueError("The annotation report is missing case/variant results.")
    jobs = [(case, variant) for case in tasks["cases"] for variant in variants]
    random.Random(seed).shuffle(jobs)
    packet_id = str(uuid.uuid4())
    private = {"schemaVersion": 1, "packetId": packet_id, "tasksSha256": tasks_hash,
               "annotationsSha256": annotations_hash, "seed": seed, "cases": tasks["cases"], "jobs": {}}
    public = []
    output.mkdir(parents=True)
    for index, (case, variant) in enumerate(jobs):
        job_id = "j-" + _digest(f"{packet_id}:{index}".encode())[:16]
        result = results.get((case["id"], variant))
        unavailable = result is not None and result["status"] == "error"
        comment = result["comment"] if result is not None and result["status"] == "comment" else None
        record = {"caseId": case["id"], "variant": variant,
                  "annotationStatus": result["status"] if result else "control", "unavailable": unavailable}
        private["jobs"][job_id] = record
        if unavailable:
            continue
        source = _with_comment(case, comment)
        folder = output / "jobs" / job_id
        folder.mkdir(parents=True)
        (folder / "source.py").write_text(source, encoding="utf-8")
        task = ("Implement the following change in source.py. Preserve existing behavior outside the request.\n"
                "Write the complete updated source to result.py. Do not edit source.py.\n\n" + case["followUpTask"] + "\n")
        (folder / "TASK.md").write_text(task, encoding="utf-8")
        record.update(sourceSha256=_digest((folder / "source.py").read_bytes()),
                      taskSha256=_digest((folder / "TASK.md").read_bytes()))
        public.append({"jobId": job_id, "task": f"jobs/{job_id}/TASK.md", "source": f"jobs/{job_id}/source.py",
                       "result": f"jobs/{job_id}/result.py"})
    _write_new(output / "annotation-cases.json", {"schemaVersion": 1, "kind": "synthetic_development",
                                                "description": "Original tasks for the synthetic later-edit experiment.",
                                                "humanReviewed": False, "cases": annotation_cases})
    _write_new(output / "requests.json", {"schemaVersion": 1, "packetId": packet_id, "jobs": public})
    _write_new(output / "private/key.json", private)
    (output / "README.md").write_text(
        "# Later-edit experiment\n\nUse a fresh coding session for EACH job in requests.json. Copy only its TASK.md and source.py "
        "to a separate disposable workspace outside this checkout. Keep the evaluator checkout outside the agent's allowed access. "
        "Keep the model, settings and tools the same across conditions. Never supply private/key.json, annotation-cases.json "
        "or the original conversation; they reveal intent, variants or test answers. Save each returned edit as result.py.\n\n"
        "Grade only in an environment appropriate for the candidate code. The explicit --allow-execution flag runs Python "
        "in subprocesses with timeouts; it is not an operating-system sandbox. Missing jobs and runtime failures remain visible. "
        "Original requirements and new task completion are scored separately; an unchanged function cannot win by preserving intent alone.\n\n"
        "Without --annotations this packet contains only no-comment controls. Generate annotation comparisons from "
        "annotation-cases.json, then export a NEW packet with --annotations to obtain all conditions.\n", encoding="utf-8")
    return {"packet": str(output), "availableJobs": len(public), "plannedJobs": len(jobs), "variants": variants}


# Input is JSON and the checks are data, not arbitrary eval expressions. Candidate
# source still executes deliberately; isolation and timeout do not create a sandbox.
CHECK_PROGRAM = r'''
import contextlib, copy, io, json, sys
payload = json.load(sys.stdin)
output = []
for check in payload["checks"]:
    try:
        args, kwargs = copy.deepcopy(check.get("args", [])), copy.deepcopy(check.get("kwargs", {}))
        original = copy.deepcopy((args, kwargs))
        namespace = {}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            exec(compile(payload["source"], "candidate.py", "exec"), namespace)
            value = namespace[check["function"]](*args, **kwargs)
            if hasattr(value, "__next__"):
                value = list(value)
        passed = value == check["expected"] and (not check.get("inputsUnchanged") or (args, kwargs) == original)
        output.append({"passed": bool(passed), "error": None})
    except BaseException as error:
        output.append({"passed": False, "error": type(error).__name__})
json.dump(output, sys.stdout)
'''


def execute_checks(source, checks, timeout=5):
    """Run only when the caller has explicitly authorized candidate execution."""
    if len(source.encode("utf-8")) > 131072:
        raise ValueError("Candidate source exceeds 128 KiB.")
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    with tempfile.TemporaryDirectory(prefix="between-the-lines-eval-") as folder:
        result = subprocess.run([sys.executable, "-I", "-c", CHECK_PROGRAM],
                                input=json.dumps({"source": source, "checks": checks}),
                                capture_output=True, text=True, cwd=folder, env=environment, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode != 0:
        raise ValueError("Candidate subprocess failed before returning check results.")
    outcomes = json.loads(result.stdout)
    if (not isinstance(outcomes, list) or len(outcomes) != len(checks)
            or any(not isinstance(item, dict) or type(item.get("passed")) is not bool for item in outcomes)):
        raise ValueError("Candidate subprocess returned an invalid check result.")
    return outcomes


def grade_retention(packet_dir, output_path, allow_execution=False):
    if not allow_execution:
        raise ValueError("Grading executes supplied result.py files. Use --allow-execution in a suitable disposable environment.")
    if Path(output_path).exists():
        raise ValueError("Choose a new grade output path.")
    packet = Path(packet_dir)
    raw = (packet / "private/key.json").read_bytes()
    key = json.loads(raw)
    requests = json.loads((packet / "requests.json").read_bytes())
    if key.get("schemaVersion") != 1 or key.get("packetId") != requests.get("packetId"):
        raise ValueError("Retention packet identity differs from its private key.")
    cases = {case["id"]: case for case in key["cases"]}
    outputs, variants = [], defaultdict(lambda: {"planned": 0, "submitted": 0, "executed": 0,
                                                "taskCompleted": 0, "originalsPreserved": 0, "jointSuccess": 0})
    for job_id, job in key["jobs"].items():
        if not re.fullmatch(r"j-[0-9a-f]{16}", job_id):
            raise ValueError("Invalid retention job path.")
        counts = variants[job["variant"]]
        counts["planned"] += 1
        item = {"jobId": job_id, "caseId": job["caseId"], "variant": job["variant"]}
        case = cases[job["caseId"]]
        folder = packet / "jobs" / job_id
        if job["unavailable"]:
            item["status"] = "annotation_error"
        elif not (folder / "result.py").is_file():
            item["status"] = "missing"
        else:
            for filename, field in (("source.py", "sourceSha256"), ("TASK.md", "taskSha256")):
                if _digest((folder / filename).read_bytes()) != job[field]:
                    raise ValueError("A job's initial source or task was modified; export a fresh packet.")
            counts["submitted"] += 1
            source = (folder / "result.py").read_text(encoding="utf-8-sig")
            item["sourceSha256"] = _digest(source.encode("utf-8"))
            try:
                outcomes = execute_checks(source, case["checks"] + case["followUpChecks"])
                originals = outcomes[:len(case["checks"])]
                follow_up = outcomes[len(case["checks"]):]
                requirement_scores = {requirement["id"]: all(outcome["passed"] for check, outcome in zip(case["checks"], originals)
                                                            if check["requirementId"] == requirement["id"])
                                      for requirement in case["requirements"]}
                preserved, completed = all(requirement_scores.values()), all(value["passed"] for value in follow_up)
                item.update(status="executed", requirements=requirement_scores, originalChecks=originals,
                            followUpChecks=follow_up, originalsPreserved=preserved, taskCompleted=completed,
                            jointSuccess=preserved and completed)
                counts["executed"] += 1
                counts["originalsPreserved"] += int(preserved)
                counts["taskCompleted"] += int(completed)
                counts["jointSuccess"] += int(preserved and completed)
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                item.update(status="execution_error", error=type(error).__name__)
        outputs.append(item)
    for counts in variants.values():
        counts["jointSuccessRateOfPlanned"] = counts["jointSuccess"] / counts["planned"]
        counts["retentionRateOfExecuted"] = counts["originalsPreserved"] / counts["executed"] if counts["executed"] else None
        counts["missingOrUnavailable"] = counts["planned"] - counts["submitted"]
    result = {"schemaVersion": 1, "packetId": key["packetId"], "privateKeySha256": _digest(raw),
              "kind": "synthetic_development", "jobs": outputs, "variants": dict(variants),
              "interpretation": "Selected behavioral checks only. No missing job is counted as preserved intent. Report task completion alongside retention; no general usefulness or reliability claim."}
    _write_new(output_path, result)
    return {"summary": str(output_path), "plannedJobs": len(outputs), "variants": dict(variants)}

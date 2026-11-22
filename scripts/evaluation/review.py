"""Export blinded human reviews and summarize labels without a model judge."""

import csv
import hashlib
import itertools
import json
from pathlib import Path
import random
import re
import statistics
import uuid


AXES = ("factuality", "intentPreservation", "usefulness", "readability")
COLUMNS = ("packetId", "candidateId", "reviewer", *AXES, "abstentionAppropriate", "notes")
STATUSES = {"comment", "abstained", "error"}
REVIEWER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

RUBRIC = """# Blind comment review

Share review.html and ratings.csv. Keep private/key.json with the evaluator: it reveals the variants. Do not open it before grading. Use a distinct reviewer ID for each person; IDs may contain letters, numbers, dots, dashes and underscores. Reviewers should work independently.

The page runs offline and sends no requests. The download button saves a filled CSV; it does not submit labels. Keep the downloaded file before closing the page. CSV can also be filled directly, retaining its IDs and header.

For a proposed comment, score every axis from 0 to 2:

| Axis | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Factuality | False or unsupported claim | Uncertain or materially overstated claim | Every claim supported by task and code |
| Intent preservation | Contradicts a relevant requirement | Omits a relevant condition, negation or constraint | Preserves all relevant intent without inventing it |
| Usefulness | Misleading, unnecessary or merely restates obvious code | Some useful context | Useful rationale or constraint worth preserving |
| Readability | Confusing or unusable | Understandable but awkward | Concise, clear and natural |

For "No comment proposed", leave the four axes blank and answer whether abstaining was appropriate (yes/no). Consider whether a useful, justified comment was possible. For "Unavailable", do not grade: a runtime failure is not an abstention. Missing and partial grades stay pending; they never become zeros or successes.

A usable comment requires factuality=2, intentPreservation=2, usefulness>=1 and readability>=1. This is an explicit review criterion, not an established product reliability threshold. The summary keeps each reviewer's rates separate and reports disagreements. AST preservation and the model's own approval are not human usefulness labels.
"""


def _read_report(path):
    raw = Path(path).read_bytes()
    report = json.loads(raw)
    if report.get("schemaVersion") != 1 or not isinstance(report.get("runId"), str):
        raise ValueError("Expected a version 1 evaluation report with a runId.")
    cases = report.get("dataset", {}).get("cases")
    results = report.get("results")
    if not isinstance(cases, list) or not isinstance(results, list):
        raise ValueError("The report needs its frozen cases and results.")
    by_id = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in by_id:
            raise ValueError("Frozen cases must have unique IDs.")
        by_id[case["id"]] = case
    seen = set()
    for result in results:
        identity = (result.get("caseId"), result.get("variant"))
        if identity in seen or identity[0] not in by_id or not isinstance(identity[1], str):
            raise ValueError("Results must have unique, known case/variant identities.")
        seen.add(identity)
        if result.get("status") not in STATUSES:
            raise ValueError("Unknown evaluation result status.")
        comment = result.get("comment")
        if result["status"] == "comment" and (not isinstance(comment, str) or not comment.strip()):
            raise ValueError("A comment result needs nonempty generated wording.")
        if result["status"] != "comment" and comment is not None:
            raise ValueError("An absent comment must be represented as null.")
    variants = report.get("settings", {}).get("variants", sorted({variant for _, variant in seen}))
    if (not isinstance(variants, list) or not variants or not all(isinstance(value, str) for value in variants)
            or len(variants) != len(set(variants))):
        raise ValueError("The report needs unique declared variants.")
    if report.get("status") == "completed" and seen != {(case_id, variant) for case_id in by_id for variant in variants}:
        raise ValueError("A completed report must include every frozen case/variant result, including errors.")
    return report, hashlib.sha256(raw).hexdigest(), by_id


def _originals(case):
    # Show source requirements, never a historical model-generated explanation.
    return [{key: item[key] for key in ("id", "symbolId", "status", "prompt", "quote", "evidence", "scopeStatus")
             if key in item} for item in case.get("knownRequirements", [])]


def create_review_packet(report_path, output_dir, reviewer, seed=42):
    """Create a new offline packet; private/key.json is kept from reviewers."""
    if not isinstance(reviewer, str) or not REVIEWER.fullmatch(reviewer):
        raise ValueError("Use a reviewer ID of 1–64 letters, numbers, dots, dashes or underscores.")
    report, report_hash, cases = _read_report(report_path)
    if report.get("status") != "completed":
        raise ValueError("Complete the evaluation run before exporting a review packet.")
    output = Path(output_dir)
    if output.exists():
        raise ValueError("Choose a new review directory; existing reviews are never overwritten.")
    packet_id = str(uuid.uuid4())
    rng = random.Random(seed)
    order = list(report["results"])
    rng.shuffle(order)
    labels = {case_id: f"Case {index + 1}" for index, case_id in enumerate(cases)}
    mapping, public = {}, []
    for index, result in enumerate(order):
        candidate_id = "c-" + hashlib.sha256(f"{packet_id}:{index}".encode()).hexdigest()[:16]
        mapping[candidate_id] = {"caseId": result["caseId"], "variant": result["variant"], "status": result["status"]}
        case = cases[result["caseId"]]
        public.append({"candidateId": candidate_id, "caseLabel": labels[result["caseId"]],
                       "language": case["language"], "prompt": case["prompt"],
                       "previousSource": case.get("previousSource"), "source": case["source"],
                       "originals": _originals(case), "status": result["status"], "comment": result["comment"]})
    payload = {"packetId": packet_id, "reviewer": reviewer, "candidates": public}
    # JSON is data inside a script element; escaping '<' prevents closing it.
    serialized = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output.mkdir(parents=True)
    (output / "private").mkdir()
    key = {"schemaVersion": 1, "packetId": packet_id, "runId": report["runId"],
           "reportSha256": report_hash, "datasetSha256": report["dataset"].get("sha256"),
           "seed": seed, "candidates": mapping}
    (output / "private/key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(RUBRIC, encoding="utf-8")
    with (output / "ratings.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows({"packetId": packet_id, "candidateId": item["candidateId"], "reviewer": reviewer} for item in public)
    (output / "review.html").write_text(HTML.replace("__PAYLOAD__", serialized), encoding="utf-8")
    return {"packetId": packet_id, "candidates": len(public), "directory": str(output),
            "reviewPage": str(output / "review.html"), "ratings": str(output / "ratings.csv")}


def _fraction(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def _usable(row):
    return row["factuality"] == 2 and row["intentPreservation"] == 2 and row["usefulness"] >= 1 and row["readability"] >= 1


def _ratings(paths, key):
    rows, seen, reviewers = {}, set(), set()
    for path in paths:
        with Path(path).open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != list(COLUMNS):
                raise ValueError("Ratings header must match the exported CSV exactly.")
            for raw in reader:
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError("A ratings row has missing or extra columns.")
                candidate_id, reviewer = raw["candidateId"], raw["reviewer"]
                if raw["packetId"] != key["packetId"] or candidate_id not in key["candidates"]:
                    raise ValueError("Ratings contain an unknown packet or candidate ID.")
                if not REVIEWER.fullmatch(reviewer):
                    raise ValueError("Every ratings row needs a valid reviewer ID.")
                identity = (reviewer, candidate_id)
                if identity in seen:
                    raise ValueError("Duplicate reviewer/candidate rating; provide one row per person and candidate.")
                seen.add(identity)
                reviewers.add(reviewer)
                status = key["candidates"][candidate_id]["status"]
                for axis in AXES:
                    if raw[axis] not in {"", "0", "1", "2"}:
                        raise ValueError(f"{axis} must be blank or the integer 0, 1 or 2.")
                answer = raw["abstentionAppropriate"]
                if answer not in {"", "yes", "no"}:
                    raise ValueError("abstentionAppropriate must be blank, yes or no.")
                if status == "comment":
                    if answer:
                        raise ValueError("Comments use the four rating axes, not an abstention label.")
                    if all(raw[axis] != "" for axis in AXES):
                        rows[identity] = {**raw, **{axis: int(raw[axis]) for axis in AXES}}
                else:
                    if any(raw[axis] for axis in AXES):
                        raise ValueError("Absent comments do not receive comment-axis ratings.")
                    if status == "error" and answer:
                        raise ValueError("A runtime error is unavailable, not a rateable abstention.")
                    if status == "abstained" and answer:
                        rows[identity] = raw
    return rows, sorted(reviewers)


def summarize_reviews(report_path, packet_dir, ratings_paths, output_path):
    """Score completed human labels with explicit denominators and disagreements."""
    report, report_hash, _ = _read_report(report_path)
    key = json.loads((Path(packet_dir) / "private/key.json").read_text(encoding="utf-8"))
    if (key.get("schemaVersion") != 1 or key.get("runId") != report["runId"]
            or key.get("reportSha256") != report_hash
            or key.get("datasetSha256") != report["dataset"].get("sha256")):
        raise ValueError("The review packet does not match this exact frozen report.")
    results = {(item["caseId"], item["variant"]): item for item in report["results"]}
    mapped = list(key.get("candidates", {}).values())
    if len(mapped) != len(results) or {(item.get("caseId"), item.get("variant")) for item in mapped} != set(results):
        raise ValueError("The private key must map every result exactly once.")
    for item in mapped:
        if item.get("status") != results[(item["caseId"], item["variant"])]["status"]:
            raise ValueError("A private-key status differs from the original result.")
    if not ratings_paths:
        raise ValueError("Provide at least one human ratings CSV, even if still blank.")
    rows, reviewers = _ratings(ratings_paths, key)
    variants = sorted({item["variant"] for item in report["results"]})
    summary = {"schemaVersion": 1, "runId": report["runId"], "packetId": key["packetId"],
               "reportSha256": report_hash, "label": "Human review of a synthetic development pilot; not held-out accuracy",
               "usableCommentCriterion": "factuality=2, intentPreservation=2, usefulness>=1, readability>=1",
               "variants": {}, "reviewers": {}, "disagreements": []}
    for variant in variants:
        entries = [item for item in report["results"] if item["variant"] == variant]
        comments = sum(item["status"] == "comment" for item in entries)
        metrics = [item.get("metrics", {}) for item in entries]
        elapsed = [item["elapsedMs"] for item in metrics if isinstance(item.get("elapsedMs"), (int, float))]
        summary["variants"][variant] = {
            "cases": len(entries), "comments": comments,
            "abstained": sum(item["status"] == "abstained" for item in entries),
            "errors": sum(item["status"] == "error" for item in entries),
            "commentCoverage": _fraction(comments, len(entries)),
            "resources": {"modelCalls": sum(item.get("modelCalls", 0) for item in metrics),
                          "inputTokens": sum(item.get("inputTokens", 0) for item in metrics),
                          "outputTokens": sum(item.get("outputTokens", 0) for item in metrics),
                          "cpuSeconds": sum(item.get("cpuSeconds", 0) for item in metrics),
                          "medianElapsedMs": statistics.median(elapsed) if elapsed else None,
                          "peakRssBytes": max((item["peakRssBytes"] for item in metrics if item.get("peakRssBytes") is not None), default=None),
                          "memoryNote": "Peak RSS is the process high-water mark, not independent per-case memory or energy."}}
    for reviewer in reviewers:
        scores = {}
        for variant in variants:
            candidates = [(candidate_id, item) for candidate_id, item in key["candidates"].items() if item["variant"] == variant]
            comments = [rows[(reviewer, candidate_id)] for candidate_id, item in candidates
                        if item["status"] == "comment" and (reviewer, candidate_id) in rows]
            abstentions = [rows[(reviewer, candidate_id)] for candidate_id, item in candidates
                           if item["status"] == "abstained" and (reviewer, candidate_id) in rows]
            total = summary["variants"][variant]
            scores[variant] = {"reviewedComments": len(comments), "reviewedAbstentions": len(abstentions),
                               "pendingComments": total["comments"] - len(comments),
                               "pendingAbstentions": total["abstained"] - len(abstentions),
                               "unavailable": total["errors"],
                               "reviewCoverage": _fraction(len(comments) + len(abstentions), total["cases"] - total["errors"]),
                               "usableComments": _fraction(sum(_usable(row) for row in comments), len(comments)),
                               "strongAnnotations": _fraction(sum(_usable(row) and row["usefulness"] == 2 for row in comments), len(comments)),
                               "axisCounts": {axis: {str(value): sum(row[axis] == value for row in comments) for value in range(3)} for axis in AXES},
                               "falseClaims": _fraction(sum(row["factuality"] == 0 for row in comments), len(comments)),
                               "incompleteIntent": _fraction(sum(row["intentPreservation"] < 2 for row in comments), len(comments)),
                               "appropriateAbstentions": _fraction(sum(row["abstentionAppropriate"] == "yes" for row in abstentions), len(abstentions)),
                               "missedUsefulComments": _fraction(sum(row["abstentionAppropriate"] == "no" for row in abstentions), len(abstentions))}
        pairs = []
        for first, second in itertools.combinations(variants, 2):
            candidate_by_case = {(item["caseId"], item["variant"]): candidate_id for candidate_id, item in key["candidates"].items()}
            wins_first = wins_second = ties = 0
            common = sorted({item["caseId"] for item in mapped if item["variant"] == first}
                            & {item["caseId"] for item in mapped if item["variant"] == second})
            for case_id in common:
                left_id, right_id = candidate_by_case[(case_id, first)], candidate_by_case[(case_id, second)]
                if key["candidates"][left_id]["status"] != "comment" or key["candidates"][right_id]["status"] != "comment":
                    continue
                left, right = rows.get((reviewer, left_id)), rows.get((reviewer, right_id))
                if left is None or right is None:
                    continue
                left_ok, right_ok = _usable(left), _usable(right)
                wins_first += left_ok and not right_ok
                wins_second += right_ok and not left_ok
                ties += left_ok == right_ok
            pairs.append({"first": first, "second": second, "commonCases": len(common),
                          "fullyReviewedCommentPairs": wins_first + wins_second + ties,
                          "firstUsableOnly": wins_first, "secondUsableOnly": wins_second, "sameUsability": ties,
                          "note": "Conditional on both variants producing fully reviewed comments; abstentions and errors are excluded."})
        summary["reviewers"][reviewer] = {"variants": scores, "pairedComparisons": pairs}
    for candidate_id, candidate in key["candidates"].items():
        available = {reviewer: rows[(reviewer, candidate_id)] for reviewer in reviewers if (reviewer, candidate_id) in rows}
        axes = AXES if candidate["status"] == "comment" else ("abstentionAppropriate",)
        differences = {axis: {reviewer: row[axis] for reviewer, row in available.items()}
                       for axis in axes if len({row[axis] for row in available.values()}) > 1}
        if differences:
            summary["disagreements"].append({"candidateId": candidate_id, "caseId": candidate["caseId"],
                                              "variant": candidate["variant"], "ratings": differences})
    output = Path(output_path)
    if output.exists():
        raise ValueError("Choose a new summary path; existing results are never overwritten.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2)
        stream.write("\n")
    return summary


HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>between-the-lines: blind comment review</title>
<style>
body{font:16px system-ui;background:#f2f8f2;color:#193627;max-width:1000px;margin:2rem auto;padding:0 1rem}h1{font-size:1.8rem}article{background:white;border:1px solid #c9dbcd;border-radius:12px;padding:1.3rem;margin:1.5rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf3ee;padding:1rem;border-radius:6px}label{display:block;margin:.7rem 0}select,input,textarea,button{font:inherit;border:1px solid #abc3b1;border-radius:5px;padding:.4rem}textarea{display:block;width:95%;min-height:4rem}button{background:#256943;color:white;cursor:pointer}small{display:block;color:#45614d}.toolbar{position:sticky;top:0;background:#f2f8f2;padding:.7rem 0;border-bottom:1px solid #c9dbcd}
</style>
<h1>Blind comment review</h1>
<p>Judge each proposal against the task and code. A model's confidence is not evidence. Read README.md for the full rubric. Leave uncertain or unfinished ratings blank.</p>
<details><summary>Rating guide</summary><p>Factuality: 0 false/unsupported, 1 uncertain/overstated, 2 all claims supported.<br>Intent: 0 contradicts, 1 misses a condition, 2 preserves relevant requirements.<br>Usefulness: 0 unnecessary/misleading, 1 some useful context, 2 rationale worth preserving.<br>Readability: 0 confusing, 1 awkward, 2 clear and concise.</p></details>
<div class="toolbar"><label>Reviewer ID <input id="reviewer" maxlength="64"></label><button id="download">Download ratings CSV</button><small id="progress"></small></div>
<main id="candidates"></main>
<script id="data" type="application/json">__PAYLOAD__</script>
<script>
'use strict';
const packet=JSON.parse(document.getElementById('data').textContent), axes=['factuality','intentPreservation','usefulness','readability'];
const reviewer=document.getElementById('reviewer'); reviewer.value=packet.reviewer;
const forms=new Map();
function element(tag,text,parent){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(parent)parent.append(node);return node;}
function block(parent,title,text){element('h3',title,parent);element('pre',text,parent);}
function progress(){let count=0;for(const [id,fields] of forms){if(fields.length&&fields.every(field=>field.value!==''))count++;}document.getElementById('progress').textContent=count+' complete of '+packet.candidates.filter(item=>item.status!=='error').length+' rateable candidates. Save your CSV before closing.';}
for(const item of packet.candidates){
 const article=element('article',undefined,document.getElementById('candidates'));
 element('h2',item.caseLabel+' · '+item.candidateId,article);element('small',item.language,article);
 block(article,'Original task',item.prompt);
 if(item.previousSource!==null&&item.previousSource!==undefined)block(article,'Before',item.previousSource);
 block(article,'Current code',item.source);
 if(item.originals.length)block(article,'Earlier recorded original context',JSON.stringify(item.originals,null,2));
 const fields=[];
 if(item.status==='comment'){
  block(article,'Proposed comment',item.comment);
  for(const name of axes){const label=element('label',name.replace(/([A-Z])/g,' $1'),article),select=element('select',undefined,label);select.dataset.axis=name;for(const value of ['','0','1','2']){const option=element('option',value===''?'Choose a score':value,select);option.value=value;}select.addEventListener('change',progress);fields.push(select);}
 }else if(item.status==='abstained'){
  element('p','No comment proposed.',article);
  const label=element('label','Was abstaining appropriate? ',article),select=element('select',undefined,label);select.dataset.axis='abstentionAppropriate';for(const value of ['','yes','no']){const option=element('option',value===''?'Choose an answer':value,select);option.value=value;}select.addEventListener('change',progress);fields.push(select);
 }else element('p','Unavailable: generation failed. This is not an abstention; do not grade it.',article);
 const notes=element('textarea',undefined,element('label','Notes',article));notes.dataset.axis='notes';
 forms.set(item.candidateId,fields);item.notes=notes;
}
function quote(value){return '"'+String(value).replace(/"/g,'""')+'"';}
document.getElementById('download').addEventListener('click',()=>{
 if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(reviewer.value)){alert('Use a reviewer ID of 1–64 letters, numbers, dots, dashes or underscores.');return;}
 const columns=['packetId','candidateId','reviewer',...axes,'abstentionAppropriate','notes'];
 const rows=packet.candidates.map(item=>{const row={packetId:packet.packetId,candidateId:item.candidateId,reviewer:reviewer.value,notes:item.notes.value};for(const field of forms.get(item.candidateId))row[field.dataset.axis]=field.value;return columns.map(name=>quote(row[name]??'')).join(',');});
 const blob=new Blob([columns.join(',')+'\r\n'+rows.join('\r\n')+'\r\n'],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='ratings-'+reviewer.value+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
progress();
</script></html>'''

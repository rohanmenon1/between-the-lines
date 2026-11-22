"""Run local comparisons, blind human review, and follow-up editing evaluations."""

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="Check cases and unique declaration targets without inference.")
    validate.add_argument("--cases", type=Path, default=ROOT / "eval/pilot-cases.json")

    run = commands.add_parser("run", help="Record actual outputs from the same local model under three conditions.")
    run.add_argument("--model-path", type=Path, required=True)
    run.add_argument("--cases", type=Path, default=ROOT / "eval/pilot-cases.json")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--variants", nargs="+", default=["between_the_lines", "prompted_comment", "code_only"],
                     choices=["between_the_lines", "prompted_comment", "code_only"])
    run.add_argument("--case-id", action="append")
    run.add_argument("--limit", type=int)
    run.add_argument("--threads", type=int, default=2)
    run.add_argument("--context-tokens", type=int, default=2048)
    run.add_argument("--max-output-tokens", type=int, default=256)
    run.add_argument("--seed", type=int, default=42)

    review = commands.add_parser("review", help="Create a shuffled offline review page and blank ratings.")
    review.add_argument("--report", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--reviewer", required=True, help="A reviewer ID, not a claim that review has happened.")
    review.add_argument("--seed", type=int, default=42)

    score = commands.add_parser("score", help="Summarize human ratings; empty ratings remain pending.")
    score.add_argument("--report", type=Path, required=True)
    score.add_argument("--packet", type=Path, required=True)
    score.add_argument("--ratings", type=Path, nargs="+", required=True)
    score.add_argument("--output", type=Path, required=True)

    prepare = commands.add_parser("retention-prepare", help="Export follow-up jobs without exposing original tasks or test answers.")
    prepare.add_argument("--tasks", type=Path, default=ROOT / "eval/retention-cases.json")
    prepare.add_argument("--annotations", type=Path, help="A completed comparison report for the exported annotation-cases.json.")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--seed", type=int, default=42)

    grade = commands.add_parser("retention-grade", help="Test returned Python edits in a subprocess; execution is explicit.")
    grade.add_argument("--packet", type=Path, required=True)
    grade.add_argument("--output", type=Path, required=True)
    grade.add_argument("--allow-execution", action="store_true",
                       help="Execute supplied result.py files. This is not an OS sandbox; use a disposable environment for untrusted code.")

    args = parser.parse_args(argv)
    from btl_agent.types import WorkerError
    try:
        if args.command == "validate":
            from evaluation.cases import load_cases
            dataset = load_cases(args.cases)
            result = {"cases": len(dataset["cases"]), "sha256": dataset["sha256"], "kind": dataset["kind"]}
        elif args.command == "run":
            from evaluation.run import run_evaluation
            report = run_evaluation(args.model_path, args.cases, args.output, args.variants,
                                    case_ids=args.case_id, limit=args.limit, threads=args.threads,
                                    context_tokens=args.context_tokens, max_output_tokens=args.max_output_tokens,
                                    seed=args.seed)
            result = {"report": str(args.output), "status": report["status"], "outputs": len(report["results"])}
        elif args.command == "review":
            from evaluation.review import create_review_packet
            create_review_packet(args.report, args.output, args.reviewer, seed=args.seed)
            result = {"review": str(args.output / "review.html"), "ratings": str(args.output / "ratings.csv")}
        elif args.command == "score":
            from evaluation.review import summarize_reviews
            summarize_reviews(args.report, args.packet, args.ratings, args.output)
            result = {"summary": str(args.output)}
        elif args.command == "retention-prepare":
            from evaluation.retention import export_retention_tasks
            result = export_retention_tasks(args.tasks, args.output, args.annotations, seed=args.seed)
        else:
            from evaluation.retention import grade_retention
            result = grade_retention(args.packet, args.output, allow_execution=args.allow_execution)
    except (OSError, ValueError, KeyError, WorkerError) as error:
        parser.exit(2, f"Evaluation stopped: {error}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

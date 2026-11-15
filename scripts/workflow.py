"""CLI for the local vertical knowledge system. Run --help for commands."""
import argparse
import json
import sys

from pipeline import Pipeline, WorkflowError, load
from model_adapter import ModelError


def summary(run):
    return {k: run[k] for k in ("id", "state", "error", "input_hash")} | {
        "artifacts": run["steps"].get("artifacts", {}), "review": run.get("review")}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/runtime.json")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "seed", "backup"):
        sub.add_parser(command)
    for command in ("admit", "add-unit"):
        sub.add_parser(command).add_argument("file")
    sub.add_parser("search").add_argument("query")
    run = sub.add_parser("run")
    run.add_argument("goal")
    run.add_argument("--key", required=True)
    resume = sub.add_parser("resume")
    resume.add_argument("id")
    resume.add_argument("--retry-model", action="store_true")
    sub.add_parser("inspect").add_argument("id")
    review = sub.add_parser("review")
    review.add_argument("id")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=("approve", "reject"), required=True)
    review.add_argument("--note", required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("id")
    publish.add_argument("--public", action="store_true")
    withdraw = sub.add_parser("withdraw")
    withdraw.add_argument("id")
    withdraw.add_argument("--reason", required=True)
    artifact = sub.add_parser("artifact")
    artifact.add_argument("id")
    artifact.add_argument("name", choices=("lesson.md", "lesson.html", "bundle.json"))
    artifact.add_argument("--release", action="store_true")
    args = parser.parse_args()
    try:
        with Pipeline(config=args.config) as app:
            if args.command in {"status", "seed", "backup"}:
                result = getattr(app, args.command)()
            elif args.command in {"admit", "add-unit"}:
                result = getattr(app, args.command.replace("-", "_"))(load(args.file))
            elif args.command == "search":
                result = app.evidence(args.query)
            elif args.command == "run":
                result = summary(app.start(args.goal, args.key))
            elif args.command == "resume":
                result = summary(app.resume(args.id, args.retry_model))
            elif args.command == "inspect":
                result = app.get_run(args.id)
            elif args.command == "review":
                result = summary(app.review(args.id, args.reviewer, args.decision, args.note))
            elif args.command == "publish":
                result = summary(app.publish(args.id, args.public))
            elif args.command == "withdraw":
                result = app.withdraw(args.id, args.reason)
            else:
                result = {"path": str(app.artifact(args.id, args.name, args.release))}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("state") in {"blocked", "failed", "invalidated"} else 0
    except (WorkflowError, ModelError, OSError, KeyError, TypeError, ValueError) as exc:
        # Known validation errors are safe; unexpected parse details are not logged with content.
        message = str(exc) if isinstance(exc, (WorkflowError, ModelError)) else type(exc).__name__
        print(json.dumps({"status": "ERROR", "error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Repeatable keyword retrieval regression, with versioned queries and failure cases."""
import argparse
import json
from pathlib import Path
import sys
import uuid

from pipeline import Pipeline, ROOT, digest, inside, load, now


def evaluate(app, suite):
    results = []
    positives = hits = negatives = refusals = 0
    for case in suite["queries"]:
        evidence = app.evidence(case["query"])
        retrieved = [e["id"] for e in evidence["entries"][:suite["top_k"]]]
        expected = set(case["expected_any"])
        passed = bool(expected.intersection(retrieved)) if expected else not retrieved
        if expected:
            positives += 1
            hits += passed
        else:
            negatives += 1
            refusals += passed
        results.append(dict(case, retrieved=retrieved, passed=passed))
    hit_rate = hits / positives if positives else 0
    return {"created": now(), "suite_version": suite["version"], "suite_hash": digest(suite),
            "input_hash": app.fingerprint(), "method": "keyword evidence retrieval, no model calls",
            "hit_at_k": hit_rate, "top_k": suite["top_k"], "positive_cases": positives,
            "no_evidence_cases": negatives, "correct_no_evidence": refusals,
            "status": "PASS" if hit_rate >= suite["minimum_hit_rate"] and refusals == negatives else "FAIL",
            "results": results, "limitations": [suite["note"], "不是Recall@K全标注评估，不代表专业审核或真实教学效果。"]}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/runtime.json")
    parser.add_argument("--suite", default="config/eval_queries.json")
    args = parser.parse_args()
    with Pipeline(config=args.config) as app:
        report = evaluate(app, load(inside(ROOT, args.suite)))
    folder = ROOT / "outputs/evaluation"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uuid.uuid4().hex + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), **report}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

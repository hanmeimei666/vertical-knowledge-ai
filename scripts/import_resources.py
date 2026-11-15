"""Prepare and curate a local resource import, then run only its confirmed knowledge."""
import argparse
import hashlib
import json
import re
import sys

from pipeline import Pipeline, ROOT, WorkflowError, digest, inside, load, now
from workflow import summary


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def batch_folder(app, batch):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch):
        raise WorkflowError("Batch ID must contain 1..80 letters/digits/underscores/hyphens")
    return inside(app.root, "data/imports/" + batch)


def unit_id(source, locator):
    return "KU-" + hashlib.sha256((source + ":" + locator).encode("utf-8")).hexdigest()[:24]


def prepare(app, manifest, batch, limit=100):
    folder = batch_folder(app, batch)
    if folder.exists():
        raise WorkflowError("Batch already exists; edit its candidates or prepare a new batch, never overwrite")
    if not 1 <= limit <= 1000:
        raise WorkflowError("Candidate limit must be 1..1000")
    for field in ("title", "provenance"):
        if not isinstance(manifest.get(field), str) or any(s in manifest[field] for s in ("请填写", "请替换")):
            raise WorkflowError("Replace manifest placeholder: " + field)
    admitted = app.admit(manifest)
    asset = next(a for a in app.records("asset") if a["id"] == admitted["id"])
    folder.mkdir(parents=True)
    snapshot = {"schema_version": 1, "batch": batch, "source": asset["id"],
                "source_revision": asset["revision"], "source_hash": asset["sha"],
                "config_hash": digest(app.config), "created": now(), "admission": admitted,
                "note": "Local curation confirmation is not professional approval"}
    write(folder / "batch.json", snapshot)
    write(folder / "manifest.json", manifest)
    candidates = []
    blocks = asset["body"].get("blocks", []) if asset["state"] == "admitted" else []
    for block in blocks[:limit]:
        candidates.append({"include": False, "confirmed": False, "knowledge": {
            "id": unit_id(asset["id"], block["locator"]), "origin": "source_excerpt",
            "title": block["text"][:60], "tags": ["资料摘录"], "summary": block["text"],
            "teaching_application": "请填写教学用法",
            "limits": "原文摘录；具体适用性需专业审核，不构成个体建议。",
            "references": [{"source": asset["id"], "locator": block["locator"]}]}})
    write(folder / "candidates.json", {"batch": batch, "units": candidates})
    result = {"batch": batch, "state": "curation_pending" if candidates else asset["state"],
              "source": asset["id"], "parsed_blocks": len(blocks), "candidates": len(candidates),
              "omitted_blocks": max(0, len(blocks)-limit),
              "candidates_file": str(folder / "candidates.json"),
              "next": "Edit included candidates, fill teaching use/tags/limits, set confirmed=true, then commit"}
    write(folder / "prepare_report.json", result)
    return result


def commit(app, batch, goal, key):
    folder = batch_folder(app, batch)
    snapshot = load(folder / "batch.json")
    if snapshot["batch"] != batch or snapshot["config_hash"] != digest(app.config):
        raise WorkflowError("Batch/config changed; prepare a new batch")
    app.fingerprint()
    asset = next((a for a in app.records("asset") if a["id"] == snapshot["source"]), None)
    if not asset or asset["state"] != "admitted" or asset["sha"] != snapshot["source_hash"] or asset["revision"] != snapshot["source_revision"]:
        raise WorkflowError("Source withdrawn, changed or quarantined; prepare a new batch")
    if any(asset["body"]["rights"][p] is not True for p in ("retrieve", "adapt")):
        raise WorkflowError("Retrieval and adaptation rights are required before committing teaching knowledge")
    document = load(folder / "candidates.json")
    if document["batch"] != batch:
        raise WorkflowError("Candidate batch mismatch")
    selected = []
    for candidate in document["units"]:
        if candidate.get("include") is not True:
            continue
        if candidate.get("confirmed") is not True:
            raise WorkflowError("Every included candidate requires explicit local curation confirmation")
        card = candidate["knowledge"]
        refs = card.get("references", [])
        if len(refs) != 1 or refs[0].get("source") != asset["id"] or card["id"] != unit_id(asset["id"], refs[0].get("locator", "")):
            raise WorkflowError("Batch units must keep their source, coordinate and derived ID")
        for field in ("title", "summary", "teaching_application", "limits"):
            if any(s in card.get(field, "") for s in ("请填写", "请替换", "待校订")):
                raise WorkflowError("Unfinished curation field: " + field)
        app.validate_unit(card)  # Validate the whole selection before any unit write.
        selected.append(card)
    if not selected or len({c["id"] for c in selected}) != len(selected):
        raise WorkflowError("Select at least one confirmed candidate, without duplicate IDs")
    # Reserve idempotency intent before writes: changing content must use a new run key.
    intent_path = folder / "commit_intents.json"
    intents = load(intent_path) if intent_path.exists() else {}
    intent_hash = digest({"cards": selected, "goal": goal})
    if key in intents and intents[key] != intent_hash:
        raise WorkflowError("Run key already used for a different curation; use a new key")
    previous = app.db.execute("SELECT id FROM runs WHERE request_key=?", (key,)).fetchone()
    if previous and key not in intents:
        raise WorkflowError("Run key already belongs to another workflow")
    intents[key] = intent_hash
    write(intent_path, intents)
    for card in selected:
        app.add_unit(card)
    result = {"batch": batch, "imported_units": len(selected),
              "run": summary(app.start(goal, key, knowledge_ids=[c["id"] for c in selected]))}
    write(folder / "commit_report.json", result)
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/runtime.json")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--manifest", required=True)
    prep.add_argument("--batch", required=True)
    prep.add_argument("--limit", type=int, default=100)
    apply = sub.add_parser("commit")
    apply.add_argument("--batch", required=True)
    apply.add_argument("--goal", required=True)
    apply.add_argument("--key", required=True)
    args = parser.parse_args()
    try:
        with Pipeline(config=args.config) as app:
            if args.command == "prepare":
                result = prepare(app, load(args.manifest), args.batch, args.limit)
                failed = result["state"] != "curation_pending"
            else:
                result = commit(app, args.batch, args.goal, args.key)
                failed = result["run"]["state"] != "draft_ready"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if failed else 0
    except (WorkflowError, OSError, ValueError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, WorkflowError) else type(exc).__name__
        print(json.dumps({"status": "ERROR", "error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""One-command clean local import demonstration; no private originals or model required."""
import json
from pathlib import Path
import shutil
import sys
import uuid

from pipeline import ROOT, Pipeline, RIGHTS, load
from import_resources import prepare, commit, write


def smoke(root=ROOT):
    root = Path(root)
    sandbox = root / "data/smoke" / uuid.uuid4().hex
    (sandbox / "config").mkdir(parents=True)
    (sandbox / "data/raw/incoming").mkdir(parents=True)
    config = load(root / "config/runtime.json")
    config["reviewer"] = None
    config["model"].update(backend="extractive", name="", allow_external=False)
    write(sandbox / "config/runtime.json", config)
    shutil.copy(root / config["rules_file"], sandbox / config["rules_file"])
    shutil.copy(root / "examples/resources/import-demo.txt", sandbox / "data/raw/incoming/import-demo.txt")
    manifest = {"id": "IMPORT-DEMO", "title": "原创导入演示", "path": "data/raw/incoming/import-demo.txt",
                "provenance": "Project-authored synthetic import fixture; internal parsing/retrieval/adaptation allowed",
                "sensitive": "none", "rights": {r: r in {"internal_parse", "retrieve", "adapt"} for r in RIGHTS}}
    with Pipeline(sandbox) as app:
        prepared = prepare(app, manifest, "demo")
        candidate_path = sandbox / "data/imports/demo/candidates.json"
        candidates = load(candidate_path)
        # Confirm only this bundled original fixture, never arbitrary user documents.
        candidate = candidates["units"][1]
        candidate.update(include=True, confirmed=True)
        candidate["knowledge"].update(title="导入演示的来源记录", tags=["导入演示"],
                                      teaching_application="检查教研记录是否能回查来源，并列出待确认问题。")
        write(candidate_path, candidates)
        imported = commit(app, "demo", "导入演示", "demo-first-draft")
        run = app.get_run(imported["run"]["id"])
        files = {name: str(app.artifact(run["id"], name)) for name in run["steps"]["artifacts"]}
        report = {"status": "PASS" if run["state"] == "draft_ready" else "FAIL",
                  "sandbox": str(sandbox), "config": str(sandbox / "config/runtime.json"),
                  "run_id": run["id"], "run_state": run["state"], "parsed_blocks": prepared["parsed_blocks"],
                  "imported_units": imported["imported_units"], "artifacts": files,
                  "model_calls": 0, "note": "Synthetic workflow validation, not professional approval or model quality"}
    write(sandbox / "smoke_report.json", report)
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = smoke()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

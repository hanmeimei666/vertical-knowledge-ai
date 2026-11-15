"""Read-only startup checks; no model calls, installation, or data upload."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "AGENTS.md", "PROJECT_BRIEF.md", "README.md", ".gitignore",
    "docs/启动配置与推进步骤.md", "docs/adr/0001-minimal-pipeline.md",
    "config/project.json", "config/asset_manifest.example.json",
    "config/runtime.json", "config/domain_rules.json", "config/eval_queries.json",
    "docs/系统流程设计与实现.md", "scripts/workflow.py", "scripts/pipeline.py", "scripts/serve.py",
    "scripts/import_resources.py", "scripts/smoke_import.py", "config/knowledge_unit.example.json",
    "docs/真实知识资源导入指南.md", "examples/resources/import-demo.txt",
)


def main():
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f"Missing file: {name}")
    for name in ("config/project.json", "config/asset_manifest.example.json", "config/runtime.json", "config/domain_rules.json", "config/eval_queries.json"):
        try:
            json.loads((ROOT / name).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            errors.append(f"Invalid configuration {name}: {exc}")
    if sys.version_info < (3, 11):
        errors.append("Python 3.11+ required for this preparation baseline")
    if not shutil.which("git"):
        errors.append("Git not found on PATH")
    print(json.dumps({
        "setup_status": "NEEDS_FIX" if errors else "SETUP_READY",
        "python": sys.version.split()[0],
        "git_available": bool(shutil.which("git")),
        "application_pipeline": "Local governed workflow implemented; run workflow.py status and evaluate.py for runtime evidence",
        "business_validation": "PROFESSIONAL_REVIEW_AND_TEACHING_EFFECTIVENESS_PENDING",
        "errors": errors,
    }, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

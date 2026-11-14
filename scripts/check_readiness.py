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
)


def main():
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f"Missing file: {name}")
    for name in ("config/project.json", "config/asset_manifest.example.json"):
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
        "application_pipeline": "NOT_IMPLEMENTED",
        "business_validation": "PENDING_REAL_SAMPLE_AND_RIGHTS",
        "errors": errors,
    }, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

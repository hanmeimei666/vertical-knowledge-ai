"""Fail CI for forbidden tracked artifacts and recognizable credential literals.

This check does not prove absence of all personal data: reviewers must inspect diffs.
Only filenames and finding categories are printed, never matched credentials.
"""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    patterns = [
        ('private_key', re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
        ('github_token', re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})')),
        ('model_token', re.compile(rb'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}')),
    ]
    findings = []
    for name in filter(None, paths):
        p = Path(name)
        forbidden = p.parts[0] in ('data', 'outputs', '.venv', 'node_modules') or '__pycache__' in p.parts
        forbidden |= p.suffix.lower() in ('.sqlite', '.sqlite3', '.db', '.pem', '.key', '.pyc', '.log')
        forbidden |= (p.name == '.env' or p.name.startswith('.env.')) and p.name != '.env.example'
        if forbidden:
            findings.append({'path': name, 'reason': 'forbidden_tracked_file'})
            continue
        content = (ROOT / p).read_bytes()
        for category, pattern in patterns:
            if pattern.search(content):
                findings.append({'path': name, 'reason': category})
    print(json.dumps({'status': 'FAIL' if findings else 'PASS', 'findings': findings}, ensure_ascii=True))
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())

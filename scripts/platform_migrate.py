"""Non-destructive SQLite migration; rollback restores an independent copy."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import uuid


def migrate(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with closing(sqlite3.connect(path)) as check:
            if check.execute('PRAGMA user_version').fetchone()[0] == 3:
                return {'database':str(path),'backup':None,'version':3,'already_current':True}
    backup = path.with_name(path.name + '.before-v03-' + uuid.uuid4().hex + '.sqlite3')
    if path.exists():
        with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
    with closing(sqlite3.connect(path)) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 3):
            raise ValueError('Unsupported database version')
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS tool_invocations(id TEXT PRIMARY KEY, tool_id TEXT, version TEXT, state TEXT, request_hash TEXT, result TEXT, created_at TEXT)')
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(3,datetime('now'))")
            db.execute('PRAGMA user_version=3')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Migration integrity failure')
    return {'database': str(path), 'backup': str(backup) if backup.exists() else None, 'version': 3}


def rollback(backup, target):
    backup, target = Path(backup).resolve(), Path(target).resolve()
    if target.exists() or target == backup:
        raise ValueError('Rollback target must be a NEW file; never overwrite an existing database')
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect('file:' + backup.as_posix() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
        if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Rollback integrity failure')
    return str(target)


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    args = parser.parse_args()
    print(json.dumps(migrate(args.database)))

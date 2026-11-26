"""Trusted local operator CLI for persistent workspaces, intake governance, impact analysis and backup/restore."""
import argparse
import json
from pathlib import Path
import sys

from pipeline import now
from platform_migrate import rollback
from readiness_demo_data import isolate
from readiness_parse import parse, span_ref
from readiness_store import Readiness


def _print(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_init(args):
    root = isolate(Path(args.workspace))
    (root / 'data/readiness/raw/staging').mkdir(parents=True, exist_ok=True)
    with Readiness(root, authorized_local_intake=True) as app:
        _print({'status': 'initialized', 'workspace': str(root), 'fts_enabled': app.fts_enabled})


def cmd_intake(args):
    meta = {
        'title': args.title,
        'provider': args.provider,
        'source_type': Path(args.staged).suffix.lstrip('.').lower(),
        'owner': args.owner,
        'sensitivity': args.sensitivity,
        'created_at': args.created_at or now(),
    }
    with Readiness(Path(args.workspace), authorized_local_intake=True) as app:
        rec = app.register_local_source(args.id, args.staged, meta)
        _print(rec)


def cmd_grant(args):
    scopes = {s.strip(): True for s in args.scopes.split(',') if s.strip()} if args.scopes else {}
    with Readiness(Path(args.workspace), authorized_local_intake=True) as app:
        rev = app.record_grant(args.id, reviewer=args.reviewer, proof=args.proof,
                               scopes=scopes, expires_at=args.expires_at, state=args.state)
        _print({'source': args.id, 'authorization_revision': rev, 'state': args.state, 'scopes': scopes})


def cmd_parse(args):
    stages = tuple(s.strip() for s in args.stages.split(',') if s.strip())
    with Readiness(Path(args.workspace), authorized_local_intake=True) as app:
        result = parse(app, args.id, args.key, stages=stages, retry=args.retry)
        _print({'id': result['id'], 'state': result['state'],
                'parts': [{'stage': p['stage'], 'segments': len(p['segments'])} for p in result['parts']]})


def cmd_draft_unit(args):
    with Readiness(Path(args.workspace), authorized_local_intake=True) as app:
        part = app.record('ParseRevision', args.extraction)
        if not part:
            raise ValueError('Unknown ParseRevision: ' + args.extraction)
        seg = next((s for s in part['body']['segments'] if s['segment_id'] == args.segment), None)
        if not seg:
            raise ValueError('Unknown segment: ' + args.segment)
        ref = {
            'source': part['body']['source'],
            'revision': part['body']['revision'],
            'extraction': args.extraction,
            'segment': seg['segment_id'],
            'locator': seg['locator'],
        }
        facets = {
            'domain': [v.strip() for v in args.domain.split(',') if v.strip()],
            'learner': [v.strip() for v in args.learner.split(',') if v.strip()],
            'training_population': [v.strip() for v in args.population.split(',') if v.strip()],
        }
        unit = app.draft_unit_from_span(args.id, ref, title=args.title, statement=args.statement, facets=facets)
        _print(unit)


def cmd_impact(args):
    with Readiness(Path(args.workspace), authorized_local_intake=args.authorized_local_intake) as app:
        report = app.impact_analysis(source_id=args.source, unit_id=args.unit)
        _print(report)


def cmd_backup(args):
    with Readiness(Path(args.workspace), authorized_local_intake=args.authorized_local_intake) as app:
        _print(app.backup())


def cmd_restore(args):
    target = Path(args.target)
    rollback(Path(args.backup), target)
    _print({'status': 'restored', 'target': str(target)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('init', help='Initialize an isolated persistent workspace')
    p.add_argument('--workspace', required=True)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser('intake', help='Register one staged local file into an authorized workspace')
    p.add_argument('--workspace', required=True)
    p.add_argument('--id', required=True)
    p.add_argument('--staged', required=True)
    p.add_argument('--title', required=True)
    p.add_argument('--provider', required=True)
    p.add_argument('--owner', required=True)
    p.add_argument('--sensitivity', default='internal')
    p.add_argument('--created-at', default=None)
    p.set_defaults(func=cmd_intake)

    p = sub.add_parser('grant', help='Record independent source authorization')
    p.add_argument('--workspace', required=True)
    p.add_argument('--id', required=True)
    p.add_argument('--reviewer', required=True)
    p.add_argument('--proof', required=True)
    p.add_argument('--scopes', default='internal_parse,retrieve,adapt')
    p.add_argument('--expires-at', required=True)
    p.add_argument('--state', default='admitted')
    p.set_defaults(func=cmd_grant)

    p = sub.add_parser('parse', help='Parse an admitted source with checkpoints')
    p.add_argument('--workspace', required=True)
    p.add_argument('--id', required=True)
    p.add_argument('--key', required=True)
    p.add_argument('--stages', default='native')
    p.add_argument('--retry', action='store_true')
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser('draft-unit', help='Draft a review_pending KnowledgeUnit from a parsed span')
    p.add_argument('--workspace', required=True)
    p.add_argument('--id', required=True)
    p.add_argument('--extraction', required=True)
    p.add_argument('--segment', required=True)
    p.add_argument('--title', required=True)
    p.add_argument('--statement', default=None)
    p.add_argument('--domain', default='conditioning')
    p.add_argument('--learner', default='educator')
    p.add_argument('--population', default='youth')
    p.set_defaults(func=cmd_draft_unit)

    p = sub.add_parser('impact', help='Run reverse lineage impact analysis')
    p.add_argument('--workspace', required=True)
    p.add_argument('--source', default=None)
    p.add_argument('--unit', default=None)
    p.add_argument('--authorized-local-intake', action='store_true')
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser('backup', help='Create an isolated SQLite backup')
    p.add_argument('--workspace', required=True)
    p.add_argument('--authorized-local-intake', action='store_true')
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser('restore', help='Restore an isolated SQLite backup to a new target path')
    p.add_argument('--backup', required=True)
    p.add_argument('--target', required=True)
    p.set_defaults(func=cmd_restore)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""V0.4+ control plane using SQLite WAL, sensitivity policy, FTS5/vector caches and reverse lineage."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import sqlite3
import uuid

from pipeline import RIGHTS, inside, now, tokens
from platform_store import Platform

PERMISSIONS = (*RIGHTS, 'ocr', 'asr', 'video_processing')
LOCAL_SCOPES = ('internal_parse', 'retrieve', 'adapt', 'ocr', 'asr', 'video_processing')
DEFAULT_SENSITIVITY_POLICY = {
    'none': list(PERMISSIONS),
    'internal': list(LOCAL_SCOPES),
    'anonymized': [*LOCAL_SCOPES, 'redistribute'],
}
STATES = {'pending_review', 'admitted', 'quarantined', 'rejected', 'revoked', 'expired'}
SOURCE_FIELDS = {'title', 'provider', 'source_type', 'owner', 'sensitivity', 'created_at'}


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('A timezone is required')
    return result


class Readiness(Platform):
    """The local operator owns the control plane; public requests only select records."""

    def __init__(self, root=None, config='config/readiness.json', *, authorized_local_intake=False):
        self.authorized_local_intake = authorized_local_intake
        self._verify_cache = None
        self._hash_checks = 0
        super().__init__(root, config)
        self._init_storage_extensions()

    def _init_storage_extensions(self):
        self.db.execute('PRAGMA busy_timeout=5000')
        try:
            self.db.execute('PRAGMA journal_mode=WAL')
        except sqlite3.OperationalError:
            pass
        self.db.executescript("""
          CREATE INDEX IF NOT EXISTS idx_records_kind_state ON records(kind, state);
          CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);
          CREATE TABLE IF NOT EXISTS unit_embeddings (
            unit_id TEXT, revision INTEGER, provider TEXT, content_hash TEXT,
            vector TEXT, created TEXT,
            PRIMARY KEY(unit_id, revision, provider, content_hash));
        """)
        try:
            self.db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS unit_fts USING fts5(unit_id UNINDEXED, content)')
            self.fts_enabled = True
        except sqlite3.OperationalError:
            self.fts_enabled = False

    def sensitivity_policy(self):
        configured = self.rules.get('sensitivity_policy') if isinstance(self.rules, dict) else None
        policy = configured if isinstance(configured, dict) and configured else DEFAULT_SENSITIVITY_POLICY
        return {level: set(scopes) & set(PERMISSIONS) for level, scopes in policy.items() if isinstance(scopes, list)}

    def max_file_size(self):
        limit = self.config.get('max_file_size_bytes', 50_000_000)
        if not isinstance(limit, int) or not 1 <= limit <= 2_000_000_000:
            raise ValueError('Invalid max_file_size_bytes configuration')
        return limit

    @contextmanager
    def verification_cache(self):
        """Deduplicate disk SHA-256 reads within a single read/search operation."""
        if self._verify_cache is not None:
            yield self._verify_cache
            return
        cache = set()
        self._verify_cache = cache
        try:
            yield cache
        finally:
            self._verify_cache = None

    def record(self, kind, id_):
        row = self.db.execute('SELECT * FROM records WHERE kind=? AND id=?', (kind, id_)).fetchone()
        return dict(row, body=json.loads(row['body'])) if row else None

    def path(self, source):
        path = inside(self.root, source['path'])
        if not path.is_relative_to(inside(self.root, self.config['raw_directory'])):
            raise PermissionError('Source must stay within the isolated raw directory')
        limit = self.max_file_size()
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError(f'Missing file or exceeds {limit} bytes')
        return path

    def generate_source(self, id_, suffix, label='Synthetic fixture'):
        from readiness_media import generate, SUFFIXES
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', id_) or suffix not in SUFFIXES:
            raise ValueError('Invalid generated source ID or format')
        prior = self.record('ReadinessSource', id_)
        parent = inside(self.root, self.config['raw_directory']) / uuid.uuid4().hex
        parent.mkdir(parents=True)
        path = parent / ('fixture' + suffix)
        generate(path, label)
        source = dict(title=label, provider='Synthetic generator', owner='local-operator',
                      source_type=suffix.lstrip('.'), created_at=now(), imported_at=now(),
                      sensitivity='none', mode='synthetic', path=path.relative_to(self.root).as_posix(),
                      sha256=sha256(path), version=1 if prior is None else prior['body']['version'] + 1)
        return self._register(id_, source)

    def register_local_source(self, id_, staged_name, metadata):
        """Trusted operator interface for the next phase, disabled in all shipped launchers."""
        from readiness_media import SUFFIXES
        if not self.authorized_local_intake:
            raise PermissionError('Authorized local intake is disabled for this workspace')
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', id_) or set(metadata) != SOURCE_FIELDS
                or any(not isinstance(v, str) or not v.strip() for v in metadata.values())):
            raise ValueError('Incomplete source metadata')
        timestamp(metadata['created_at'])
        raw = inside(self.root, self.config['raw_directory'])
        staging = inside(raw, 'staging')
        path = inside(staging, staged_name)
        if path.parent != staging or path.suffix.lower() not in SUFFIXES:
            raise ValueError('Select one supported local staged file')
        self.path({'path': path.relative_to(self.root).as_posix()})
        prior = self.record('ReadinessSource', id_)
        directory = raw / uuid.uuid4().hex
        directory.mkdir()
        copy = directory / ('original' + path.suffix.lower())
        shutil.copyfile(path, copy)
        return self._register(id_, dict(metadata, imported_at=now(), mode='authorized-local',
                                        path=copy.relative_to(self.root).as_posix(), sha256=sha256(copy),
                                        version=prior['body']['version'] + 1 if prior else 1))

    def _register(self, id_, source):
        path = self.path(source)
        if (source['mode'] not in {'synthetic', 'authorized-local'} or not SOURCE_FIELDS <= source.keys()
                or sha256(path) != source['sha256']):
            raise ValueError('Incomplete source origin')
        duplicates = [r for r in self.records('ReadinessSource')
                      if r['id'] != id_ and r['body']['sha256'] == source['sha256']]
        if duplicates:
            raise ValueError('Duplicate content: ' + duplicates[0]['id'])
        self.put('ReadinessSource', id_, source, 'pending_review')
        return self.record('ReadinessSource', id_)

    def record_grant(self, id_, *, reviewer, proof, scopes, expires_at, state='admitted'):
        source = self.record('ReadinessSource', id_)
        expiry = timestamp(expires_at)
        if (not source or not reviewer.strip() or not proof.strip() or state not in STATES
                or set(scopes) - set(PERMISSIONS) or any(type(v) is not bool for v in scopes.values())):
            raise ValueError('Incomplete authorization record')
        if state == 'admitted':
            if expiry <= datetime.now(timezone.utc):
                raise ValueError('An admission requires unexpired authorization')
            source_body = source['body']
            allowed_scopes = self.sensitivity_policy().get(source_body['sensitivity'])
            if allowed_scopes is None or sha256(self.path(source_body)) != source_body['sha256']:
                raise PermissionError('Cannot approve modified or sensitive fixture')
            requested_true = {k for k, v in scopes.items() if v is True}
            if requested_true - allowed_scopes:
                raise PermissionError('Sensitivity policy forbids requested scope: '
                                      + ','.join(sorted(requested_true - allowed_scopes)))
        policy = dict(reviewer=reviewer, proof=proof, scopes=dict(scopes), expires_at=expires_at,
                      source_revision=source['revision'], source_sha256=source['body']['sha256'],
                      source_version=source['body']['version'], reviewed_at=now())
        return self.put('SourceAuthorization', id_, policy, state)

    def source_status(self, source):
        grant = self.record('SourceAuthorization', source['id'])
        if not grant or grant['body']['source_revision'] != source['revision']:
            return 'pending_review'
        if grant['state'] == 'admitted' and timestamp(grant['body']['expires_at']) <= datetime.now(timezone.utc):
            return 'expired'
        return grant['state']

    def authorize(self, source_id, *scopes, revision=None):
        source = self.record('ReadinessSource', source_id)
        if not source or self.source_status(source) != 'admitted' or revision not in (None, source['revision']):
            raise PermissionError('Source, version or authorization not current')
        b = source['body']
        grant = self.record('SourceAuthorization', source_id)['body']
        modes = {'synthetic', 'authorized-local'} if self.authorized_local_intake else {'synthetic'}
        allowed_scopes = self.sensitivity_policy().get(b['sensitivity'])
        if (b['mode'] not in modes or allowed_scopes is None or grant['source_sha256'] != b['sha256']
                or grant['source_version'] != b['version'] or not grant['reviewer'] or not grant['proof']
                or set(scopes) - set(PERMISSIONS)
                or set(scopes) - allowed_scopes
                or any(grant['scopes'].get(s) is not True for s in scopes)):
            raise PermissionError('Missing independent source permission')
        cache_key = (source_id, source['revision'], b['sha256'])
        if self._verify_cache is None or cache_key not in self._verify_cache:
            self._hash_checks += 1
            if sha256(self.path(b)) != b['sha256']:
                raise PermissionError('Source fingerprint changed')
            if self._verify_cache is not None:
                self._verify_cache.add(cache_key)
        return source

    def evidence_span(self, ref, purpose='retrieve'):
        source = self.authorize(ref['source'], 'retrieve', purpose, revision=ref['revision'])
        kind = 'ParseRevision' if ref.get('layer', 'machine') == 'machine' else 'SpanCorrection'
        event = self.record(kind, ref['extraction'])
        if not event or event['state'] != 'completed':
            raise ValueError('Unknown extraction')
        self.authorize(ref['source'], *event['body'].get('permissions', ()), revision=ref['revision'])
        matching = [s for s in event['body']['segments'] if s['segment_id'] == ref['segment']]
        if (event['body']['source'] != source['id'] or event['body']['revision'] != source['revision']
                or len(matching) != 1 or matching[0]['locator'] != ref['locator']):
            raise ValueError('Source locator binding mismatch')
        return matching[0]

    def sync_unit_fts(self, unit_id, span_texts=None):
        if not self.fts_enabled:
            return
        unit = self.record('ReadinessUnit', unit_id)
        with self.db:
            self.db.execute('DELETE FROM unit_fts WHERE unit_id=?', (unit_id,))
            if not unit or unit['state'] not in {'approved', 'review_pending'}:
                return
            u = unit['body']
            parts = [u['title'], u['statement']]
            if span_texts is None:
                for ref in u['references']:
                    try:
                        parts.append(self.evidence_span(ref)['extracted_content'])
                    except (PermissionError, ValueError, OSError):
                        pass
            else:
                parts.extend(span_texts)
            doc_tokens = ' '.join(sorted(tokens(' '.join(parts))))
            self.db.execute('INSERT INTO unit_fts(unit_id, content) VALUES(?, ?)', (unit_id, doc_tokens))

    def rebuild_fts(self):
        if not self.fts_enabled:
            return 0
        with self.db:
            self.db.execute('DELETE FROM unit_fts')
        count = 0
        with self.verification_cache():
            for row in self.records('ReadinessUnit'):
                self.sync_unit_fts(row['id'])
                count += 1
        return count

    def save_unit(self, unit):
        required = {'id', 'title', 'statement', 'facets', 'references', 'objectives', 'prerequisites', 'risks'}
        if (set(unit) - (required | {'valid_until', 'trace', 'training_units', 'claim_key', 'claim_value'})
                or not required <= unit.keys() or not unit['references']
                or any(not isinstance(unit[k], list) for k in ('objectives', 'prerequisites', 'risks'))
                or not isinstance(unit['facets'], dict)
                or any(not isinstance(v, list) or not all(isinstance(x, str) for x in v) for v in unit['facets'].values())
                or any(not isinstance(unit[k], str) or not unit[k].strip() for k in ('id', 'title', 'statement'))):
            raise ValueError('Invalid candidate knowledge unit')
        if unit.get('valid_until'):
            timestamp(unit['valid_until'])
        span_texts = [self.evidence_span(ref)['extracted_content'] for ref in unit['references']]
        rev = self.put('ReadinessUnit', unit['id'], unit, 'review_pending')
        self.sync_unit_fts(unit['id'], span_texts)
        return rev

    def draft_unit_from_span(self, unit_id, ref, *, title, statement=None, facets,
                             objectives=None, prerequisites=None, risks=None):
        """Create a review_pending candidate KnowledgeUnit anchored to a verified span."""
        span = self.evidence_span(ref)
        draft_statement = (statement or span['extracted_content']).strip()
        if not draft_statement:
            raise ValueError('Span has no extractable text; provide an explicit statement')
        unit = {
            'id': unit_id,
            'title': title.strip(),
            'statement': draft_statement[:2000],
            'facets': facets,
            'objectives': list(objectives or []),
            'prerequisites': list(prerequisites or []),
            'risks': list(risks or []),
            'references': [ref],
        }
        self.save_unit(unit)
        return self.record('ReadinessUnit', unit_id)

    def review_unit(self, id_, reviewer, decision, note):
        unit = self.record('ReadinessUnit', id_)
        if (not unit or not reviewer.strip() or not note.strip()
                or decision not in {'approved', 'rejected', 'withdrawn'}):
            raise ValueError('Explicit operator decision and rationale required')
        span_texts = None
        if decision == 'approved':
            if unit['state'] != 'review_pending':
                raise ValueError('Only current candidates may be approved')
            span_texts = [self.evidence_span(ref)['extracted_content'] for ref in unit['body']['references']]
        self.put('UnitReview', id_, dict(reviewer=reviewer, note=note, unit_revision=unit['revision'],
                                        decided_at=now()), decision)
        rev = self.put('ReadinessUnit', id_, unit['body'], decision)
        self.sync_unit_fts(id_, span_texts)
        return rev

    def relate(self, left, predicate, right):
        from platform_store import RELATIONS
        if (left == right or predicate not in RELATIONS
                or not all(self.record('ReadinessUnit', id_) for id_ in (left, right))):
            raise ValueError('Unknown relation or endpoint')
        from pipeline import digest
        id_ = digest([left, predicate, right])
        return self.put('ReadinessRelation', id_, dict(subject=left, predicate=predicate, object=right), 'candidate')

    def merge_units(self, target, members, reviewer, note):
        if target['id'] in members or len(set(members)) < 2 or not reviewer.strip() or not note.strip():
            raise ValueError('Merge requires distinct inputs and an operator rationale')
        originals = [self.record('ReadinessUnit', id_) for id_ in dict.fromkeys(members)]
        if any(not r or r['state'] not in {'approved', 'review_pending'} for r in originals):
            raise ValueError('Merge input unavailable')
        references = {repr(sorted(x.items())): x for r in originals for x in r['body']['references']}
        self.save_unit(dict(target, references=list(references.values()), trace=members))
        for id_ in dict.fromkeys(members):
            self.relate(target['id'], 'supersedes', id_)
            self.review_unit(id_, reviewer, 'withdrawn', note)
        return self.record('ReadinessUnit', target['id'])

    def impact_analysis(self, *, source_id=None, unit_id=None):
        """Compute full downstream blast radius for a source or knowledge unit."""
        if bool(source_id) == bool(unit_id):
            raise ValueError('Specify exactly one of source_id or unit_id')
        target_sources = set()
        target_units = set()
        if source_id:
            source = self.record('ReadinessSource', source_id)
            if not source:
                raise ValueError('Unknown source: ' + source_id)
            target_sources.add(source_id)
        else:
            unit = self.record('ReadinessUnit', unit_id)
            if not unit:
                raise ValueError('Unknown unit: ' + unit_id)
            target_units.add(unit_id)

        parse_runs = [r['id'] for r in self.records('ParseRun') if r['body'].get('source') in target_sources]
        parse_revisions = [r['id'] for r in self.records('ParseRevision') if r['body'].get('source') in target_sources]
        corrections = [r['id'] for r in self.records('SpanCorrection') if r['body'].get('source') in target_sources]
        media_assets = [r['id'] for r in self.records('ReadinessMediaAsset') if r['body'].get('source') in target_sources]

        direct_units = []
        for row in self.records('ReadinessUnit'):
            refs = row['body'].get('references', [])
            if row['id'] in target_units or any(ref.get('source') in target_sources for ref in refs):
                direct_units.append({'id': row['id'], 'revision': row['revision'], 'state': row['state'],
                                     'title': row['body'].get('title', '')})
                target_units.add(row['id'])

        related_units = set()
        impacted_relations = []
        for rel in self.records('ReadinessRelation'):
            b = rel['body']
            if b['subject'] in target_units or b['object'] in target_units:
                impacted_relations.append(b)
                related_units.update([b['subject'], b['object']])
        related_units -= target_units

        impacted_runs = []
        for row in self.db.execute('SELECT id FROM runs ORDER BY created DESC'):
            run = self.get_run(row['id'], check=False)
            steps = run.get('steps', {})
            entries = steps.get('evidence', {}).get('entries', [])
            timeline = steps.get('timeline', [])
            hit_unit = any(e.get('knowledge', {}).get('id') in target_units for e in entries)
            hit_source = (any(ref.get('source') in target_sources for e in entries for ref in e.get('references', []))
                          or any(clip.get('source') in target_sources for clip in timeline))
            if hit_unit or hit_source:
                impacted_runs.append({
                    'id': run['id'],
                    'track': run.get('request', {}).get('track'),
                    'state': run['state'],
                    'artifacts': sorted((steps.get('artifacts') or {}).keys()),
                })

        return {
            'target': {'kind': 'source', 'id': source_id} if source_id else {'kind': 'unit', 'id': unit_id},
            'parse_runs': parse_runs,
            'parse_revisions': parse_revisions,
            'corrections': corrections,
            'media_assets': media_assets,
            'direct_units': direct_units,
            'related_unit_ids': sorted(related_units),
            'relations': impacted_relations,
            'runs': impacted_runs,
        }

    def invalidate_impacted_runs(self, *, source_id=None, unit_id=None, reviewer, note):
        """Actively mark impacted active runs as invalidated with an audit entry."""
        if not reviewer.strip() or not note.strip():
            raise ValueError('Reviewer and note are required for cascade invalidation')
        report = self.impact_analysis(source_id=source_id, unit_id=unit_id)
        invalidated = []
        for item in report['runs']:
            if item['state'] in {'draft_ready', 'approved', 'published'}:
                run = self.get_run(item['id'], check=False)
                run['state'] = 'invalidated'
                run['error'] = f'cascade_invalidated_by_{report["target"]["kind"]}:{report["target"]["id"]}'
                self.save_run(run)
                invalidated.append(item['id'])
        self.audit('impact_invalidation', report['target']['id'],
                   {'target': report['target'], 'reviewer': reviewer, 'note': note, 'invalidated_runs': invalidated})
        return {'report': report, 'invalidated_runs': invalidated}

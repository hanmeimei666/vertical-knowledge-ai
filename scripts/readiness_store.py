"""V0.4 control plane using the existing SQLite records and revision journal."""
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import uuid

from pipeline import RIGHTS, inside, now
from platform_store import Platform

PERMISSIONS = (*RIGHTS, 'ocr', 'asr', 'video_processing')
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
        super().__init__(root, config)

    def record(self, kind, id_):
        row = self.db.execute('SELECT * FROM records WHERE kind=? AND id=?',(kind,id_)).fetchone()
        return dict(row,body=json.loads(row['body'])) if row else None

    def path(self, source):
        path = inside(self.root, source['path'])
        if not path.is_relative_to(inside(self.root, self.config['raw_directory'])):
            raise PermissionError('Source must stay within the isolated raw directory')
        if not path.is_file() or path.stat().st_size > 50_000_000:
            raise ValueError('Missing file or exceeds 50 MB')
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

    def register_local_source(self,id_,staged_name,metadata):
        """Trusted operator interface for the next phase, disabled in all shipped launchers."""
        from readiness_media import SUFFIXES
        if not self.authorized_local_intake:
            raise PermissionError('Authorized local intake is disabled for this workspace')
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',id_) or set(metadata)!=SOURCE_FIELDS
                or any(not isinstance(v,str) or not v.strip() for v in metadata.values())):
            raise ValueError('Incomplete source metadata')
        timestamp(metadata['created_at'])
        raw = inside(self.root, self.config['raw_directory'])
        staging = inside(raw,'staging')
        path = inside(staging,staged_name)
        if path.parent != staging or path.suffix.lower() not in SUFFIXES:
            raise ValueError('Select one supported local staged file')
        self.path({'path':path.relative_to(self.root).as_posix()})
        prior = self.record('ReadinessSource',id_)
        directory = raw / uuid.uuid4().hex
        directory.mkdir()
        copy = directory / ('original' + path.suffix.lower())
        shutil.copyfile(path,copy)
        return self._register(id_,dict(metadata,imported_at=now(),mode='authorized-local',
                                       path=copy.relative_to(self.root).as_posix(),sha256=sha256(copy),
                                       version=prior['body']['version']+1 if prior else 1))

    def _register(self, id_, source):
        path = self.path(source)
        if (source['mode'] not in {'synthetic','authorized-local'} or not SOURCE_FIELDS <= source.keys()
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
            if source_body['sensitivity'] != 'none' or sha256(self.path(source_body)) != source_body['sha256']:
                raise PermissionError('Cannot approve modified or sensitive fixture')
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
        modes = {'synthetic','authorized-local'} if self.authorized_local_intake else {'synthetic'}
        if (b['mode'] not in modes or b['sensitivity'] != 'none' or grant['source_sha256'] != b['sha256']
                or grant['source_version'] != b['version'] or not grant['reviewer'] or not grant['proof']
                or set(scopes) - set(PERMISSIONS) or any(grant['scopes'].get(s) is not True for s in scopes)):
            raise PermissionError('Missing independent source permission')
        if sha256(self.path(b)) != b['sha256']:
            raise PermissionError('Source fingerprint changed')
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
        for ref in unit['references']:
            self.evidence_span(ref)
        return self.put('ReadinessUnit', unit['id'], unit, 'review_pending')

    def review_unit(self, id_, reviewer, decision, note):
        unit = self.record('ReadinessUnit', id_)
        if (not unit or not reviewer.strip() or not note.strip()
                or decision not in {'approved', 'rejected', 'withdrawn'}):
            raise ValueError('Explicit operator decision and rationale required')
        if decision == 'approved':
            if unit['state'] != 'review_pending':
                raise ValueError('Only current candidates may be approved')
            for ref in unit['body']['references']:
                self.evidence_span(ref)
        self.put('UnitReview', id_, dict(reviewer=reviewer, note=note, unit_revision=unit['revision'],
                                        decided_at=now()), decision)
        return self.put('ReadinessUnit', id_, unit['body'], decision)

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

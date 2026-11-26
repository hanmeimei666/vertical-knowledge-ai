"""Revision- and permission-scoped lexical (FTS5/IDF), cached vector and hybrid retrieval."""
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from urllib import error, request
from urllib.parse import urlsplit

from pipeline import digest, now, tokens
from readiness_store import timestamp

ALIASES = {'amber': 'gold', 'azure': 'blue', 'emerald': 'green', 'crimson': 'red',
           'pathway': 'sequence', 'workshop': 'classroom'}
LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}


def is_loopback_url(endpoint):
    parsed = urlsplit(endpoint)
    return parsed.scheme in {'http', 'https'} and parsed.hostname in LOOPBACK_HOSTS


class EmbeddingProvider(ABC):
    requires_external_processing = False

    @property
    def provider_id(self):
        return type(self).__name__

    @abstractmethod
    def embed(self, text):
        """Return a vector under an explicit local provider configuration."""


class SyntheticEmbedding(EmbeddingProvider):
    def embed(self, text):
        v = [0] * 128
        for token in tokens(text):
            key = int(digest(ALIASES.get(token, token))[:8], 16) % len(v)
            v[key] += 1
        return v


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Real local Ollama embeddings adapter using stdlib urllib (loopback by default)."""

    def __init__(self, model='bge-m3', endpoint='http://127.0.0.1:11434/api/embeddings',
                 timeout=30, allow_external=False):
        if not model or not isinstance(model, str):
            raise ValueError('Embedding model name is required')
        if not is_loopback_url(endpoint) and not allow_external:
            raise PermissionError('Non-loopback embedding endpoint requires allow_external=True')
        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout
        self.requires_external_processing = not is_loopback_url(endpoint)

    @property
    def provider_id(self):
        return f'ollama:{self.model}'

    def embed(self, text):
        payload = json.dumps({'model': self.model, 'prompt': text[:8000]}).encode('utf-8')
        req = request.Request(self.endpoint, data=payload, headers={'Content-Type': 'application/json'})
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'Ollama embedding failed: {type(exc).__name__}') from None
        vector = data.get('embedding') or (data.get('embeddings', [None])[0] if isinstance(data.get('embeddings'), list) else None)
        if not isinstance(vector, list) or not vector or not all(isinstance(x, (int, float)) for x in vector):
            raise ValueError('Invalid Ollama embedding vector response')
        return [float(x) for x in vector]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Real OpenAI-compatible /v1/embeddings adapter with explicit external gating."""

    def __init__(self, model, endpoint='http://127.0.0.1:8000/v1/embeddings',
                 api_key_env='OPENAI_API_KEY', timeout=30, allow_external=False):
        if not model or not isinstance(model, str):
            raise ValueError('Embedding model name is required')
        if not is_loopback_url(endpoint) and not allow_external:
            raise PermissionError('External embedding endpoint requires allow_external=True')
        self.model = model
        self.endpoint = endpoint
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.requires_external_processing = not is_loopback_url(endpoint)

    @property
    def provider_id(self):
        return f'openai-compat:{self.model}'

    def embed(self, text):
        headers = {'Content-Type': 'application/json'}
        api_key = os.environ.get(self.api_key_env, '') if self.api_key_env else ''
        if self.requires_external_processing and not api_key:
            raise PermissionError(f'Missing API key environment variable: {self.api_key_env}')
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        payload = json.dumps({'model': self.model, 'input': text[:8000]}).encode('utf-8')
        req = request.Request(self.endpoint, data=payload, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'Embedding request failed: {type(exc).__name__}') from None
        items = data.get('data') if isinstance(data, dict) else None
        vector = items[0].get('embedding') if isinstance(items, list) and items and isinstance(items[0], dict) else None
        if not isinstance(vector, list) or not vector or not all(isinstance(x, (int, float)) for x in vector):
            raise ValueError('Invalid OpenAI-compatible embedding response')
        return [float(x) for x in vector]


class VectorSearch:
    def __init__(self, provider):
        self.provider = provider
        self.cache_hits = 0
        self.cache_misses = 0

    def _embed_cached(self, text, *, db=None, unit_id=None, revision=None):
        if db is None or unit_id is None or revision is None:
            self.cache_misses += 1
            return self.provider.embed(text)
        content_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        provider_id = getattr(self.provider, 'provider_id', type(self.provider).__name__)
        row = db.execute(
            'SELECT vector FROM unit_embeddings WHERE unit_id=? AND revision=? AND provider=? AND content_hash=?',
            (unit_id, revision, provider_id, content_hash),
        ).fetchone()
        if row:
            self.cache_hits += 1
            return json.loads(row['vector'])
        self.cache_misses += 1
        vec = self.provider.embed(text)
        with db:
            db.execute(
                'INSERT OR REPLACE INTO unit_embeddings VALUES(?, ?, ?, ?, ?, ?)',
                (unit_id, revision, provider_id, content_hash, json.dumps(vec), now()),
            )
        return vec

    def score(self, query, corpus, *, db=None, revisions=None):
        q = self.provider.embed(query)
        nq = math.hypot(*q)
        revisions = revisions or {}
        result = {}
        for id_, text in corpus.items():
            v = self._embed_cached(text, db=db, unit_id=id_, revision=revisions.get(id_))
            if len(v) != len(q):
                raise ValueError('Embedding dimensions do not match')
            length = math.hypot(*v)
            result[id_] = sum(a * b for a, b in zip(q, v)) / (nq * length) if nq and length else 0.0
        return result


def lexical(query, corpus):
    fields = {key: tokens(text) for key, text in corpus.items()}
    words = tokens(query)
    df = Counter(w for values in fields.values() for w in values if w in words)
    return {id_: sum(math.log(1 + len(fields) / (1 + df[w])) for w in values & words)
            for id_, values in fields.items()}


def _fts_candidates(app, query):
    """Fast FTS5 OR pre-filter returning matching unit IDs when FTS5 is synchronized."""
    if not getattr(app, 'fts_enabled', False) or not query.strip():
        return None
    words = sorted(tokens(query))
    if not words:
        return set()
    active_count = app.db.execute(
        "SELECT COUNT(*) FROM records WHERE kind='ReadinessUnit' AND state IN ('approved','review_pending')"
    ).fetchone()[0]
    fts_count = app.db.execute('SELECT COUNT(*) FROM unit_fts').fetchone()[0]
    if fts_count != active_count:
        return None
    match_expr = ' OR '.join('"' + w.replace('"', '') + '"' for w in words[:32])
    rows = app.db.execute('SELECT unit_id FROM unit_fts WHERE unit_fts MATCH ?', (match_expr,)).fetchall()
    return {r['unit_id'] for r in rows}


def search(app, query='', filters=None, *, mode='AND', purpose='retrieve', method='lexical',
           versions=None, states=('approved',), expand=False, limit=10, vectors=None, rerank=None):
    if (mode not in {'AND', 'OR'} or purpose not in {'retrieve', 'adapt'} or not 1 <= limit <= 50
            or method not in {'lexical', 'semantic', 'hybrid'} or not isinstance(query, str) or len(query) > 2000
            or not set(states) <= {'approved', 'review_pending'}):
        raise ValueError('Invalid search')
    filters = filters or {}
    if not isinstance(filters, dict) or any(not isinstance(v, list) or not all(isinstance(x, str) for x in v)
                                            for v in filters.values()):
        raise ValueError('Filters are facet arrays')

    vector_engine = None
    requires_external = False
    if query and method != 'lexical':
        vector_engine = vectors or VectorSearch(SyntheticEmbedding())
        provider = getattr(vector_engine, 'provider', None)
        requires_external = bool(getattr(provider, 'requires_external_processing', False))

    fts_ids = _fts_candidates(app, query) if (query and method == 'lexical' and not expand) else None
    rows = {}
    corpus = {}
    revisions = {}
    now_utc = datetime.now(timezone.utc)
    with app.verification_cache():
        for row in app.records('ReadinessUnit'):
            if fts_ids is not None and row['id'] not in fts_ids:
                continue
            u = row['body']
            expiry = u.get('valid_until')
            if (row['state'] not in states or expiry and timestamp(expiry) <= now_utc
                    or versions and row['id'] in versions and versions[row['id']] != row['revision']):
                continue
            checks = [bool(set(values) & set(u['facets'].get(dimension, []))) for dimension, values in filters.items()]
            if checks and not (all(checks) if mode == 'AND' else any(checks)):
                continue
            try:
                spans = [app.evidence_span(ref, purpose) for ref in u['references']]
                if requires_external:
                    for ref in u['references']:
                        app.authorize(ref['source'], 'external_model_processing', revision=ref['revision'])
            except (ValueError, PermissionError, OSError):
                continue
            rows[row['id']] = {'knowledge': u, 'version': row['revision'], 'review_state': row['state'],
                               'references': u['references'], 'relations': []}
            corpus[row['id']] = ' '.join([u['title'], u['statement'], *(s['extracted_content'] for s in spans)])
            revisions[row['id']] = row['revision']

    scores = lexical(query, corpus)
    if query and method != 'lexical':
        if isinstance(vector_engine, VectorSearch):
            semantic = vector_engine.score(query, corpus, db=app.db, revisions=revisions)
        else:
            semantic = vector_engine.score(query, corpus)
        if set(semantic) != set(corpus):
            raise ValueError('Vector provider changed corpus IDs')
        if method == 'semantic':
            scores = {key: score if score >= .15 else 0 for key, score in semantic.items()}
        else:
            ceiling = max(scores.values(), default=0) or 1
            scores = {key: scores[key] / ceiling + (.55 * score if score >= .15 else 0) for key, score in semantic.items()}

    ranked = sorted((id_ for id_, score in scores.items() if score > 0 or not query), key=lambda id_: (-scores[id_], id_))
    if rerank:
        revised = list(rerank(query, tuple(ranked), dict(scores)))
        if Counter(revised) != Counter(ranked):
            raise ValueError('Reranker must preserve authorized candidates')
        ranked = revised
    relations = [r['body'] for r in app.records('ReadinessRelation')
                 if r['state'] == 'candidate' and r['body']['subject'] in rows and r['body']['object'] in rows]
    direct = set(ranked)
    if expand:
        ranked += sorted({r['object'] for r in relations if r['subject'] in direct} - direct)
    entries = []
    for id_ in ranked[:limit]:
        entries.append(dict(rows[id_], score=scores[id_], match='direct' if id_ in direct else 'relation',
                            relations=[r for r in relations if id_ in (r['subject'], r['object'])]))
    claims = {}
    for item in entries:
        u = item['knowledge']
        if u.get('claim_key'):
            claims.setdefault(u['claim_key'], {}).setdefault(u.get('claim_value', ''), []).append(u['id'])
    conflicts = [dict(claim=key, alternatives=values) for key, values in claims.items() if len(values) > 1]
    conflicts += [r for r in relations if r['predicate'] == 'contradicts' and r['subject'] in ranked[:limit]
                  and r['object'] in ranked[:limit]]
    return {'id': 'ev-' + digest(entries)[:20], 'mode': method, 'entries': entries, 'conflicts': conflicts,
            'gaps': [] if entries else ['no_authorized_matching_evidence']}

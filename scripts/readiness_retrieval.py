"""Revision- and permission-scoped lexical, vector and hybrid retrieval."""
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime, timezone
import math
import re

from pipeline import digest, tokens
from readiness_store import timestamp

ALIASES = {'amber':'gold', 'azure':'blue', 'emerald':'green', 'crimson':'red',
           'pathway':'sequence', 'workshop':'classroom'}


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text):
        """Return a vector under an explicit local provider configuration."""


class SyntheticEmbedding(EmbeddingProvider):
    def embed(self,text):
        v = [0]*128
        for token in tokens(text):
            key = int(digest(ALIASES.get(token,token))[:8],16) % len(v)
            v[key] += 1
        return v


class VectorSearch:
    def __init__(self, provider):
        self.provider = provider

    def score(self, query, corpus):
        q = self.provider.embed(query)
        nq = math.hypot(*q)
        result = {}
        for id_, text in corpus.items():
            v = self.provider.embed(text)
            if len(v) != len(q):
                raise ValueError('Embedding dimensions do not match')
            length = math.hypot(*v)
            result[id_] = sum(a*b for a,b in zip(q,v))/(nq*length) if nq and length else 0.0
        return result


def lexical(query, corpus):
    fields = {key:tokens(text) for key,text in corpus.items()}
    words = tokens(query)
    df = Counter(w for values in fields.values() for w in values if w in words)
    return {id_:sum(math.log(1+len(fields)/(1+df[w])) for w in values & words)
            for id_, values in fields.items()}


def search(app, query='', filters=None, *, mode='AND', purpose='retrieve', method='lexical',
           versions=None, states=('approved',), expand=False, limit=10, vectors=None, rerank=None):
    if (mode not in {'AND','OR'} or purpose not in {'retrieve','adapt'} or not 1 <= limit <= 50
            or method not in {'lexical','semantic','hybrid'} or not isinstance(query,str) or len(query)>2000
            or not set(states) <= {'approved','review_pending'}):
        raise ValueError('Invalid search')
    filters = filters or {}
    if not isinstance(filters,dict) or any(not isinstance(v,list) or not all(isinstance(x,str) for x in v)
                                          for v in filters.values()):
        raise ValueError('Filters are facet arrays')
    rows = {}
    corpus = {}
    for row in app.records('ReadinessUnit'):
        u = row['body']
        expiry = u.get('valid_until')
        if (row['state'] not in states or expiry and timestamp(expiry) <= datetime.now(timezone.utc)
                or versions and row['id'] in versions and versions[row['id']] != row['revision']):
            continue
        checks = [bool(set(values)&set(u['facets'].get(dimension,[]))) for dimension,values in filters.items()]
        if checks and not (all(checks) if mode=='AND' else any(checks)):
            continue
        try:
            spans = [app.evidence_span(ref,purpose) for ref in u['references']]
        except (ValueError, PermissionError, OSError):
            continue
        rows[row['id']] = {'knowledge':u,'version':row['revision'],'review_state':row['state'],
                           'references':u['references'],'relations':[]}
        corpus[row['id']] = ' '.join([u['title'],u['statement'],*(s['extracted_content'] for s in spans)])
    scores = lexical(query,corpus)
    if query and method!='lexical':
        semantic = (vectors or VectorSearch(SyntheticEmbedding())).score(query,corpus)
        if set(semantic) != set(corpus):
            raise ValueError('Vector provider changed corpus IDs')
        if method == 'semantic':
            scores = {key:score if score>=.15 else 0 for key,score in semantic.items()}
        else:
            ceiling = max(scores.values(),default=0) or 1
            scores = {key:scores[key]/ceiling + (.55*score if score>=.15 else 0) for key,score in semantic.items()}
    ranked = sorted((id_ for id_,score in scores.items() if score>0 or not query),key=lambda id_:(-scores[id_],id_))
    if rerank:
        revised = list(rerank(query,tuple(ranked),dict(scores)))
        if Counter(revised)!=Counter(ranked):
            raise ValueError('Reranker must preserve authorized candidates')
        ranked = revised
    relations = [r['body'] for r in app.records('ReadinessRelation')
                 if r['state'] == 'candidate' and r['body']['subject'] in rows and r['body']['object'] in rows]
    direct = set(ranked)
    if expand:
        ranked += sorted({r['object'] for r in relations if r['subject'] in direct}-direct)
    entries = []
    for id_ in ranked[:limit]:
        entries.append(dict(rows[id_], score=scores[id_], match='direct' if id_ in direct else 'relation',
                            relations=[r for r in relations if id_ in (r['subject'],r['object'])]))
    claims = {}
    for item in entries:
        u = item['knowledge']
        if u.get('claim_key'):
            claims.setdefault(u['claim_key'],{}).setdefault(u.get('claim_value',''),[]).append(u['id'])
    conflicts = [dict(claim=key, alternatives=values) for key,values in claims.items() if len(values)>1]
    conflicts += [r for r in relations if r['predicate']=='contradicts' and r['subject'] in ranked[:limit]
                  and r['object'] in ranked[:limit]]
    return {'id':'ev-'+digest(entries)[:20], 'mode':method, 'entries':entries, 'conflicts':conflicts,
            'gaps':[] if entries else ['no_authorized_matching_evidence']}

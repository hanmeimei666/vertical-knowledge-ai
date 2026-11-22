"""Shared faceted assets atop the existing revision and audit mechanism."""
from pathlib import Path
import hashlib
import json

from pipeline import Pipeline, now, digest, inside
from platform_contracts import contract
from platform_migrate import migrate
from platform_tools import ToolRegistry, ParserRegistry

RELATIONS='is_a part_of related_to prerequisite_of supports explains demonstrates assesses applicable_to not_applicable_to derived_from contradicts supersedes'.split()


class Platform(Pipeline):
    def __init__(self,root=None,config='config/runtime.json'):
        if root is None:
            from pipeline import ROOT
            root=ROOT
        super().__init__(root,config)
        self.migration=migrate(inside(self.root,self.config['database']))
        self.tools=ToolRegistry(self.db)
        self.parsers=ParserRegistry(self.tools)

    def save_object(self,kind,value):
        contract(kind,value)
        id_=value['id']
        if kind=='TaxonomyTerm':
            dimensions={r['id']:r for r in self.records('TaxonomyDimension')}
            if value['dimension_id'] not in dimensions: raise ValueError('Unknown dimension')
            terms={r['id']:r['body'] for r in self.records(kind)}
            for parent in value['parents']:
                if parent==id_ or parent not in terms or terms[parent]['dimension_id']!=value['dimension_id']:
                    raise ValueError('Invalid taxonomy parent')
                pending=[parent];seen=set()
                while pending:
                    p=pending.pop()
                    if p==id_: raise ValueError('Taxonomy cycle')
                    if p not in seen: seen.add(p);pending.extend(terms[p]['parents'])
        if kind=='KnowledgeRelation':
            dictionary=set(RELATIONS)|{r['id'] for r in self.records('RelationType')}
            ids={r['id'] for k in ('KnowledgeUnit','TaxonomyTerm','Concept') for r in self.records(k)}
            if value['predicate'] not in dictionary or value['subject'] not in ids or value['object'] not in ids:
                raise ValueError('Invalid relation reference/type')
            if value['review_state']=='approved': raise ValueError('Use separate expert review; models cannot approve relations')
        if kind=='KnowledgeUnit':
            if value['review_state']!='review_pending': raise ValueError('Knowledge enters review_pending only')
            terms={r['id']:r['body'] for r in self.records('TaxonomyTerm')}
            for term in value['taxonomy_assignments']:
                if term not in terms or terms[term]['status']!='active': raise ValueError('Unknown/inactive taxonomy term')
            if not value['evidence_references']: raise ValueError('Evidence required')
            for ref in value['evidence_references']:
                asset=self.asset(ref['source_asset_id'])
                if ref['source_revision']!=asset['revision']: raise ValueError('Stale evidence revision')
                if not any(s['segment_id']==ref['segment_id'] and s['locator']==ref['locator'] for s in asset['body'].get('segments',[])):
                    raise ValueError('Unknown evidence locator')
        return self.put(kind,id_,value,value.get('review_state',value.get('status','active')))

    def asset(self,id_):
        asset=next((r for r in self.records('asset') if r['id']==id_),None)
        if not asset: raise ValueError('Unknown source')
        return asset

    def fingerprint(self):
        implementation={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('platform_*.py')}
        return digest({'legacy':super().fingerprint(),'implementation':implementation,
            'objects':[tuple(r) for r in self.db.execute('SELECT kind,id,revision,sha,state FROM records ORDER BY kind,id')]})

    def extract(self,manifest,key):
        if manifest.get('synthetic') is not True: raise ValueError('V0.3 validation accepts generated synthetic fixtures only')
        if manifest.get('sensitive')!='none': raise ValueError('Sensitive/unknown data quarantined; no extraction')
        if manifest.get('rights',{}).get('internal_parse') is not True: raise PermissionError('Explicit internal_parse required')
        from pipeline import RIGHTS
        if not set(RIGHTS)<=set(manifest['rights']) or any(v is not None and type(v) is not bool for v in manifest['rights'].values()):
            raise ValueError('All six rights plus optional processing permissions must be explicit')
        path=inside(self.root,manifest['path'])
        if not path.is_relative_to(inside(self.root,self.config['raw_directory'])): raise ValueError('Outside raw directory')
        if path.stat().st_size>50_000_000: raise ValueError('File exceeds 50MB local limit')
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        body=dict(manifest,sha256=sha)
        previous=next((r for r in self.records('asset') if r['id']==manifest['id']),None)
        old_body={k:v for k,v in previous['body'].items() if k!='segments'} if previous else None
        revision=previous['revision'] if old_body==body else previous['revision']+1 if previous else 1
        tool=self.parsers.resolve(path)
        result=self.tools.invoke(tool,{'root':str(self.root),'path':manifest['path'],'raw_directory':self.config['raw_directory'],'source':manifest['id'],'revision':revision},manifest['rights'],key)
        if result['state']!='completed': return result
        segments=result['output']['segments']
        for segment in segments:
            contract('ExtractedSegment',dict(segment,id=segment['segment_id'],schema_version='0.3',version=1))
        body['segments']=segments
        # Existing put() versions only when complete content differs.
        actual=self.put('asset',manifest['id'],body,'admitted')
        if actual!=revision: raise ValueError('Concurrent revision changed; retry in single-writer mode')
        extraction={'id':key,'schema_version':'0.3','version':1,'source_asset_id':manifest['id'],'source_revision':actual,'status':'candidate','tool_result':result['id']}
        self.save_object('ExtractionRun',extraction)
        return {'state':'candidate','source_revision':actual,'segments':segments,'tool_invocation':key}

    def confirm(self,unit,confirmed=False):
        if confirmed is not True: raise ValueError('Explicit candidate curation confirmation required')
        return self.save_object('KnowledgeUnit',unit)

    def retrieve(self,query='',filters=None,mode='AND',purpose='retrieve',relations=None):
        if mode not in ('AND','OR') or purpose not in ('retrieve','adapt'): raise ValueError('Invalid query mode/purpose')
        self.fingerprint()
        terms={r['id']:r['body'] for r in self.records('TaxonomyTerm')}
        entries=[]
        for row in self.records('KnowledgeUnit'):
            u=row['body'];refs=u['evidence_references'];valid=True
            for ref in refs:
                a=self.asset(ref['source_asset_id'])
                if a['state']!='admitted' or a['revision']!=ref['source_revision'] or a['body']['rights'].get('retrieve') is not True or a['body']['rights'].get(purpose) is not True:
                    valid=False;break
            if not valid: continue
            checks=[]
            for dimension,selected in (filters or {}).items():
                assigned={t for t in u['taxonomy_assignments'] if terms.get(t,{}).get('dimension_id')==dimension and terms[t]['status']=='active'}
                checks.append(bool(assigned & set(selected)))
            if checks and not (all(checks) if mode=='AND' else any(checks)): continue
            if query and query.lower() not in (u['title']+' '+u['statement']).lower(): continue
            edges=[r['body'] for r in self.records('KnowledgeRelation') if r['body']['subject']==u['id'] and (not relations or r['body']['predicate'] in relations)]
            entries.append({'knowledge':u,'references':refs,'relations':edges,'applicability_warning':'Labels do not establish professional applicability'})
        return {'id':'evidence-'+digest(entries)[:12],'schema_version':'0.3','version':1,'entries':entries,'gaps':['No automatic professional applicability or conflict resolution'], 'status':'evidence_candidates' if entries else 'no_evidence'}

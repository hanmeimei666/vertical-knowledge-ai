"""Shared faceted assets atop the existing revision and audit mechanism."""
from pathlib import Path
import hashlib
import json
import re
import uuid

from pipeline import Pipeline, RIGHTS, now, digest, inside
from platform_fixtures import GENERATOR, write_fixture
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
        self.tools=ToolRegistry(self.db,self._authorize_tool)
        self.parsers=ParserRegistry(self.tools)

    def create_fixture(self,suffix,id_):
        """Privileged test setup: generate fixed bytes and record origin, never bless an existing file."""
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',id_):raise ValueError('Invalid fixture ID')
        if any(r['id']==id_ for r in self.records('asset')):raise ValueError('Source already exists')
        directory=inside(self.root,self.config['raw_directory'])/'generated'/uuid.uuid4().hex
        directory.mkdir(parents=True)
        if suffix not in ('.txt','.md','.html','.csv','.json','.docx','.pptx','.xlsx','.wav'):
            raise ValueError('Unsupported fixed fixture format')
        path=directory/('fixture'+suffix);write_fixture(path)
        relative=path.relative_to(self.root).as_posix();sha=hashlib.sha256(path.read_bytes()).hexdigest()
        manifest={'id':id_,'title':'Synthetic fixture','path':relative,'synthetic':True,'sensitive':'none',
            'provenance':GENERATOR,'rights':{r:r in ('internal_parse','retrieve','adapt') for r in RIGHTS}}
        manifest['rights'].update(ocr=False,asr=False,video_processing=False,external_processing=False)
        self.put('fixture_origin',id_,{'generator':GENERATOR,'path':relative,'sha256':sha},'generated')
        self.put('asset',id_,dict(manifest,sha256=sha),'admitted')
        return manifest

    def _authorize_source(self,id_,permissions):
        try:a=self.asset(id_)
        except ValueError:raise PermissionError('Unknown source') from None
        b=a['body']
        if a['state']!='admitted':raise PermissionError('Source must be admitted')
        if b.get('sensitive')!='none':raise PermissionError('Unknown/unsupported source sensitivity')
        origin=next((r for r in self.records('fixture_origin') if r['id']==id_),None)
        if b.get('synthetic') is not True or not origin or origin['state']!='generated':
            raise PermissionError('Trusted generated source record required')
        proof=origin['body']
        if proof.get('generator')!=GENERATOR or proof.get('path')!=b.get('path') or proof.get('sha256')!=b.get('sha256'):
            raise PermissionError('Generated source binding mismatch')
        rights=b.get('rights',{})
        if not set(RIGHTS)<=set(rights) or any(v is not None and type(v) is not bool for v in rights.values()):
            raise PermissionError('Explicit independent source permissions required')
        for p in permissions:
            if rights.get(p) is not True:raise PermissionError('Missing source permission: '+p)
        path=inside(self.root,b['path'])
        if not path.is_relative_to(inside(self.root,self.config['raw_directory'])):raise PermissionError('Source path binding mismatch')
        if not path.is_file() or path.stat().st_size>50_000_000 or hashlib.sha256(path.read_bytes()).hexdigest()!=proof['sha256']:
            raise PermissionError('Generated source hash mismatch')
        return a

    def _authorize_tool(self,d,payload,source_ids):
        """Ordinary arguments select records; only local generated records authorize execution."""
        permissions=set(d['permission_requirements']);adapter=d['adapter_key']
        if adapter=='native_parse':
            a=self._authorize_source(payload['source'],permissions|{'internal_parse'})
            expected={'root':str(self.root),'path':a['body']['path'],'raw_directory':self.config['raw_directory'],
                'source':a['id'],'revision':self._extraction_revision(a)}
            bound=dict(payload,root=str(Path(payload['root']).resolve()))
            if bound!=expected or source_ids not in (None,[a['id']]):raise PermissionError('Parser payload binding mismatch')
        elif adapter=='export_draft':
            run=self.get_run(payload['run']['id'])
            if run['state'] in ('invalidated','blocked'):raise PermissionError('Export run is not current')
            bound=dict(payload,root=str(Path(payload['root']).resolve()))
            if bound!={'root':str(self.root),'config':str(self.config_path.relative_to(self.root)),'run':run}:
                raise PermissionError('Export payload binding mismatch')
            refs=[r for e in run['steps'].get('evidence',{}).get('entries',[]) for r in e['references']]
            if not refs:raise PermissionError('Export source references required')
            ids=sorted({r['source'] for r in refs})
            if source_ids not in (None,ids):raise PermissionError('Export source binding mismatch')
            for ref in refs:
                a=self._authorize_source(ref['source'],permissions|{'retrieve','adapt'})
                if ref['source_revision']!=a['revision']:raise PermissionError('Export revision binding mismatch')
        elif adapter=='echo':
            if not isinstance(source_ids,list) or len(source_ids)!=1:raise PermissionError('One generated probe source required')
            a=self._authorize_source(source_ids[0],permissions|{'internal_parse'})
            path=inside(self.root,a['body']['path'])
            if path.suffix!='.json' or payload!={'value':json.loads(path.read_text(encoding='utf-8'))}:
                raise PermissionError('Probe payload binding mismatch')
        else:raise PermissionError('No governed payload binding for adapter')

    def _extraction_revision(self,asset):
        segments=asset['body'].get('segments',[])
        current=segments and all(s['source_revision']==asset['revision'] for s in segments)
        return asset['revision'] if current else asset['revision']+1

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
        if not set(RIGHTS)<=set(manifest['rights']) or any(v is not None and type(v) is not bool for v in manifest['rights'].values()):
            raise ValueError('All six rights plus optional processing permissions must be explicit')
        admitted=self._authorize_source(manifest['id'],{'internal_parse'})
        expected={k:v for k,v in admitted['body'].items() if k not in ('segments','sha256')}
        if manifest!=expected:raise PermissionError('Manifest does not match trusted source record')
        path=inside(self.root,manifest['path'])
        if not path.is_relative_to(inside(self.root,self.config['raw_directory'])): raise ValueError('Outside raw directory')
        if path.stat().st_size>50_000_000: raise ValueError('File exceeds 50MB local limit')
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        body=dict(manifest,sha256=sha)
        revision=self._extraction_revision(admitted)
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
                try:a=self._authorize_source(ref['source_asset_id'],{'retrieve',purpose})
                except (PermissionError,ValueError,OSError):valid=False;break
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

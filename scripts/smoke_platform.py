"""Generated non-professional fixtures only. Never reads historical knowledge assets."""
import json
from pathlib import Path
import shutil
import uuid
from pipeline import ROOT, load
from platform_store import Platform
from platform_production import produce


def obj(id_,**fields):return dict(id=id_,schema_version='0.3',version=1,**fields)


def smoke():
    root=ROOT/'data/v03-smoke'/uuid.uuid4().hex
    (root/'config').mkdir(parents=True);raw=root/'data/raw';raw.mkdir(parents=True)
    for f in ('runtime.json','domain_rules.json'):shutil.copy(ROOT/'config'/f,root/'config'/f)
    with Platform(root) as app:
        for dimension in ('population','domain','scenario','goal','media','applicability'):
            app.save_object('TaxonomyDimension',obj(dimension,label=dimension,status='active'))
        for term,dimension in [('alpha','domain'),('beta','domain'),('group','population'),('place','scenario')]:
            app.save_object('TaxonomyTerm',obj(term,dimension_id=dimension,label=term,parents=[],status='active'))
        parsed={}
        for i,suffix in enumerate(('.txt','.md','.html','.csv','.json','.docx','.pptx','.xlsx','.wav')):
            manifest=app.create_fixture(suffix,'SYN-'+str(i))
            result=app.extract(manifest,'extraction-'+str(i));parsed[suffix]=result['state']
            if suffix=='.txt':
                segment=result['segments'][0];reference={'source_asset_id':manifest['id'],'source_revision':result['source_revision'],'segment_id':segment['segment_id'],'locator':segment['locator']}
        unit=obj('SYN-K',title='Synthetic alpha',content_type='statement',statement='Synthetic alpha structural record, no professional conclusion.',
            explanation='',concepts=[],taxonomy_assignments=['alpha','beta','group','place'],population_scope=['group'],domain_scope=['alpha','beta'],scenario_scope=['place'],activity_scope=[],prerequisites=[],applicability={'status':'unknown'},contraindications=[],safety_constraints=['expert review pending'],evidence_level='synthetic',evidence_references=[reference],media_references=[],review_state='review_pending',validity={'status':'test_only'})
        app.confirm(unit,True)
        app.save_object('KnowledgeRelation',obj('synthetic-relation',subject='SYN-K',predicate='related_to',object='alpha',review_state='candidate',evidence_references=[reference]))
        request=obj('synthetic-request',goal='Synthetic',project={'filters':{'domain':['beta'],'population':['group']}},domains=['alpha','beta'],populations=['group'],scenarios=['place'],outputs=['markdown','html','json'],evidence={},available_tools=['export.draft'],restrictions={'review_required':True,'network':False})
        run=produce(app,request,'synthetic-production')
        report={'status':'PASS' if run['state']=='draft_ready' else 'FAIL','root':str(root),'parsed':parsed,
            'shared_alpha':len(app.retrieve('',{'domain':['alpha']})['entries']),
            'shared_beta':len(app.retrieve('',{'domain':['beta']})['entries']),
            'run':run['id'],'state':run['state'],'artifacts':run['steps'].get('artifact_contracts',[]),
            'tools':[{k:h[k] for k in ('tool_id','status','reason')} for h in app.tools.discover()],
            'model_calls':0,'professional_content_created':False}
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    import sys
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    report=smoke();print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(report['status']!='PASS')

"""Generated non-professional fixtures only. Never reads historical knowledge assets."""
import json
from pathlib import Path
import shutil
import uuid
import wave
import zipfile
from pipeline import ROOT, RIGHTS, load
from platform_store import Platform
from platform_production import produce


def obj(id_,**fields):return dict(id=id_,schema_version='0.3',version=1,**fields)


def smoke():
    root=ROOT/'data/v03-smoke'/uuid.uuid4().hex
    (root/'config').mkdir(parents=True);raw=root/'data/raw';raw.mkdir(parents=True)
    for f in ('runtime.json','domain_rules.json'):shutil.copy(ROOT/'config'/f,root/'config'/f)
    files={'.txt':'Synthetic alpha', '.md':'# Synthetic alpha', '.html':'<h1>Synthetic alpha</h1>', '.csv':'label,value\nSynthetic alpha,1', '.json':'{"label":"Synthetic alpha"}'}
    for suffix,content in files.items():(raw/('fixture'+suffix)).write_text(content,encoding='utf-8')
    with zipfile.ZipFile(raw/'fixture.docx','w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic alpha</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(raw/'fixture.pptx','w') as z:z.writestr('ppt/slides/slide1.xml','<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Synthetic alpha</a:t></p:sld>')
    with zipfile.ZipFile(raw/'fixture.xlsx','w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Synthetic" r:id="r1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>Synthetic alpha</t></is></c></row></sheetData></worksheet>')
    with wave.open(str(raw/'fixture.wav'),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*80)
    with Platform(root) as app:
        for dimension in ('population','domain','scenario','goal','media','applicability'):
            app.save_object('TaxonomyDimension',obj(dimension,label=dimension,status='active'))
        for term,dimension in [('alpha','domain'),('beta','domain'),('group','population'),('place','scenario')]:
            app.save_object('TaxonomyTerm',obj(term,dimension_id=dimension,label=term,parents=[],status='active'))
        parsed={}
        for i,path in enumerate(sorted(raw.iterdir())):
            manifest={'id':'SYN-'+str(i),'title':'Synthetic fixture','path':path.relative_to(root).as_posix(),
                'synthetic':True,'sensitive':'none','provenance':'Generated test, no professional claims',
                'rights':{r:r in ('internal_parse','retrieve','adapt') for r in RIGHTS}}
            result=app.extract(manifest,'extraction-'+str(i));parsed[path.suffix]=result['state']
            if path.suffix=='.txt':
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

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
import wave
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from pipeline import ROOT, RIGHTS
from platform_store import Platform
from platform_contracts import contract
from platform_migrate import migrate,rollback
from platform_tools import definition
from platform_parsers import parse_file
from platform_production import produce


def obj(id_,**fields):
    return dict(id=id_,schema_version='0.3',version=1,**fields)


class PlatformChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'config').mkdir();(self.root/'data/raw').mkdir(parents=True)
        for name in ('runtime.json','domain_rules.json'):shutil.copy(ROOT/'config'/name,self.root/'config'/name)
        self.app=Platform(self.root)

    def tearDown(self):
        self.app.db.close();self.temp.cleanup()

    def populate(self):
        p=self.root/'data/raw/synthetic.txt';p.write_text('Synthetic alpha record, no professional conclusion.',encoding='utf-8')
        manifest={'id':'SYN','title':'Synthetic','path':'data/raw/synthetic.txt','synthetic':True,'sensitive':'none','provenance':'program-generated',
                  'rights':{r:r in ('internal_parse','retrieve','adapt') for r in RIGHTS}}
        ex=self.app.extract(manifest,'extract-one')
        self.assertEqual(ex['state'],'candidate')
        for dimension in ('population','domain','scenario'):
            self.app.save_object('TaxonomyDimension',obj(dimension,label=dimension,status='active'))
        for id_,dimension in [('alpha','domain'),('beta','domain'),('group','population'),('place','scenario')]:
            self.app.save_object('TaxonomyTerm',obj(id_,dimension_id=dimension,label=id_,parents=[],status='active'))
        segment=ex['segments'][0]
        unit=obj('K-SYN',title='Synthetic alpha',content_type='statement',statement='Synthetic alpha structure only.',taxonomy_assignments=['alpha','beta','group','place'],
            applicability={'unknown':['all professional applicability']},evidence_references=[{'source_asset_id':'SYN','source_revision':ex['source_revision'],'segment_id':segment['segment_id'],'locator':segment['locator']}],review_state='review_pending',evidence_level='synthetic')
        self.app.confirm(unit,True)
        return manifest,unit

    def test_schema_migration_preserves_and_rolls_back(self):
        path=self.root/'old.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE records(id TEXT, body TEXT)');db.execute("INSERT INTO records VALUES('historical','untouched')");db.commit()
        result=migrate(path)
        restored=self.root/'restored.sqlite3';rollback(result['backup'],restored)
        for target in (path,restored):
            with closing(sqlite3.connect(target)) as db:self.assertEqual(db.execute('SELECT * FROM records').fetchone(),('historical','untouched'))
        with self.assertRaises(ValueError):rollback(result['backup'],path)

    def test_facets_shared_assets_and_directional_relations(self):
        manifest,u=self.populate()
        self.app.save_object('KnowledgeRelation',obj('rel',subject='K-SYN',predicate='related_to',object='alpha',review_state='candidate',evidence_references=[]))
        for domain in ('alpha','beta'):
            pack=self.app.retrieve('Synthetic',{'domain':[domain],'population':['group']})
            self.assertEqual(len(pack['entries']),1);self.assertEqual(pack['entries'][0]['references'][0]['source_asset_id'],'SYN')
        self.assertEqual(len(self.app.retrieve('',{'domain':['alpha'],'scenario':['missing']},'OR')['entries']),1)
        self.assertEqual(self.app.retrieve('',{'domain':['alpha'],'scenario':['missing']},'AND')['entries'],[])
        self.app.withdraw('SYN','synthetic revocation')
        self.assertEqual(self.app.retrieve()['entries'],[])

    def test_candidate_confirmation_and_source_versions(self):
        manifest,u=self.populate()
        with self.assertRaises(ValueError):self.app.confirm(dict(u,id='new'),False)
        manifest['rights']['adapt']=False
        self.app.extract(manifest,'extract-two')
        self.assertEqual(self.app.retrieve()['entries'],[])

    def test_tool_health_schema_policy_timeout_and_idempotency(self):
        self.app.tools.register(definition('echo','echo','testing'))
        r=self.app.tools.invoke('echo',{'value':1},{},'once');self.assertEqual(r['state'],'completed')
        self.assertEqual(self.app.tools.invoke('echo',{'value':1},{},'once'),r)
        with self.assertRaises(ValueError):self.app.tools.invoke('echo',{'value':2},{},'once')
        self.assertEqual(self.app.tools.invoke('echo',{}, {},'bad-input')['state'],'failed')
        d=definition('restricted','echo','testing',['ocr']);self.app.tools.register(d)
        self.assertEqual(self.app.tools.invoke('restricted',{'value':1},{'ocr':None},'denied')['state'],'failed')
        d=definition('unknown','untrusted_string','testing');self.app.tools.register(d)
        self.assertEqual(self.app.tools.health('unknown')['status'],'failed_health_check')
        d=definition('timeout','echo','testing');d['timeout']=0.001;self.app.tools.register(d)
        self.assertEqual(self.app.tools.invoke('timeout',{'value':1},{},'timeout')['state'],'failed')
        self.assertEqual(self.app.tools.health('ocr')['status'],'planned')

    def test_taxonomy_cycles_and_unreviewed_relations(self):
        self.populate()
        self.app.save_object('TaxonomyTerm',obj('child',dimension_id='domain',label='child',parents=['alpha'],status='active'))
        with self.assertRaises(ValueError):self.app.save_object('TaxonomyTerm',obj('alpha',dimension_id='domain',label='alpha',parents=['child'],status='active'))
        with self.assertRaises(ValueError):self.app.save_object('KnowledgeRelation',obj('bad',subject='K-SYN',predicate='supports',object='alpha',review_state='approved',evidence_references=[]))

    def test_negative_schema_dependency_and_path_controls(self):
        d=definition('missing','echo','test',dependencies=['nonexistent_v03_dependency_xyz'])
        self.app.tools.register(d)
        self.assertEqual(self.app.tools.health('missing')['status'],'unavailable_dependency')
        d=definition('wrong-output','echo','test');d['output_schema']={'type':'string'}
        self.app.tools.register(d)
        self.assertEqual(self.app.tools.invoke('wrong-output',{'value':1},{},'wrong-output')['state'],'failed')
        d=definition('network','echo','test');d['network_access']=True
        self.app.tools.register(d)
        self.assertEqual(self.app.tools.invoke('network',{'value':1},{},'network')['state'],'failed')
        with self.assertRaises(ValueError):self.app.save_object('KnowledgeUnit',obj('incomplete',title='Synthetic'))
        with self.assertRaises(ValueError):self.app.extract({'synthetic':False},'real-data-denied')
        with self.assertRaises(ValueError):self.app.parsers.resolve('sample.mp4')

    def test_complete_production_and_idempotent_recovery(self):
        self.populate()
        request=obj('req',goal='Synthetic',project={'filters':{'domain':['beta']}},domains=['alpha','beta'],populations=['group'],scenarios=['place'],outputs=['markdown','html','json'],evidence={},available_tools=['export.draft'],restrictions={'professional_review':True})
        run=produce(self.app,request,'production')
        self.assertEqual(run['state'],'draft_ready')
        for name in run['steps']['artifacts']:self.assertTrue(self.app.artifact(run['id'],name).exists())
        self.assertEqual(produce(self.app,request,'production')['id'],run['id'])
        self.assertEqual(len(run['steps']['artifact_contracts']),3)

    def test_all_native_document_and_media_metadata_parsers(self):
        raw=self.root/'data/raw'
        for suffix,content in {'.txt':'synthetic','.md':'# synthetic','.html':'<h1>Synthetic</h1><script>hidden()</script>', '.csv':'label,value\nsynthetic,1','.json':'{"synthetic":{"value":1}}'}.items():
            (raw/('sample'+suffix)).write_text(content,encoding='utf-8')
        with zipfile.ZipFile(raw/'sample.docx','w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic</w:t></w:r></w:p></w:body></w:document>')
        with zipfile.ZipFile(raw/'sample.pptx','w') as z:z.writestr('ppt/slides/slide1.xml','<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Synthetic</a:t></p:sld>')
        with zipfile.ZipFile(raw/'sample.xlsx','w') as z:
            z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Synthetic" r:id="r1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>Synthetic</t></is></c></row></sheetData></worksheet>')
        with wave.open(str(raw/'sample.wav'),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*80)
        for p in raw.iterdir():
            result=parse_file(p,'SYN',1);self.assertTrue(result['segments'],p.suffix)
            self.assertTrue(all(s['locator'] for s in result['segments']))

    def test_optional_pdf_and_image(self):
        import importlib.util
        if importlib.util.find_spec('pypdf'):
            from pypdf import PdfWriter
            from pypdf.generic import DecodedStreamObject,NameObject,DictionaryObject
            writer=PdfWriter();page=writer.add_blank_page(width=300,height=300)
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 100 Td (Synthetic) Tj ET');page[NameObject('/Contents')]=writer._add_object(stream)
            path=self.root/'data/raw/sample.pdf';writer.write(path)
            self.assertIn('Synthetic',parse_file(path,'SYN',1)['segments'][0]['extracted_content'])
        if importlib.util.find_spec('PIL'):
            from PIL import Image
            path=self.root/'data/raw/sample.png';Image.new('RGB',(4,4)).save(path)
            self.assertEqual(parse_file(path,'SYN',1)['segments'][0]['modality'],'image')


if __name__=='__main__':unittest.main()

from contextlib import closing
from copy import deepcopy
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
from pipeline import ROOT
from platform_store import Platform
from platform_contracts import contract
from platform_migrate import migrate,rollback
from platform_tools import definition
from platform_parsers import parse_file
from platform_production import produce, register_exporter


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
        manifest=self.app.create_fixture('.txt','SYN')
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
        body=deepcopy(self.app.asset('SYN')['body']);body['rights']=manifest['rights']
        self.app.put('asset','SYN',body,'admitted')
        self.app.extract(manifest,'extract-two')
        self.assertEqual(self.app.retrieve()['entries'],[])

    def test_tool_health_schema_policy_timeout_and_idempotency(self):
        self.app.create_fixture('.json','PROBE')
        self.app.tools.register(definition('echo','echo','testing'))
        r=self.app.tools.invoke('echo',{'value':1},{},'once',source_ids=['PROBE']);self.assertEqual(r['state'],'completed')
        self.assertEqual(self.app.tools.invoke('echo',{'value':1},{},'once',source_ids=['PROBE']),r)
        with self.assertRaises(ValueError):self.app.tools.invoke('echo',{'value':2},{},'once',source_ids=['PROBE'])
        self.assertEqual(self.app.tools.invoke('echo',{}, {},'bad-input',source_ids=['PROBE'])['state'],'failed')
        d=definition('restricted','echo','testing',['ocr']);self.app.tools.register(d)
        self.assertEqual(self.app.tools.invoke('restricted',{'value':1},{'ocr':None},'denied',source_ids=['PROBE'])['state'],'failed')
        d=definition('unknown','untrusted_string','testing');self.app.tools.register(d)
        self.assertEqual(self.app.tools.health('unknown')['status'],'failed_health_check')
        d=definition('timeout','echo','testing');d['timeout']=0.001;self.app.tools.register(d)
        self.assertEqual(self.app.tools.invoke('timeout',{'value':1},{},'timeout',source_ids=['PROBE'])['state'],'failed')
        self.assertEqual(self.app.tools.health('ocr')['status'],'planned')

    def test_tool_registration_is_idempotent_and_rejects_conflicts(self):
        d=definition('registered','echo','testing',['retrieve'])
        self.app.tools.register(d)
        self.app.tools.register(deepcopy(d))
        changes={'permission_requirements':['adapt'],'adapter_key':'native_parse',
            'input_schema':{'type':'string'},'output_schema':{'type':'string'},
            'data_sensitivity':'unknown','timeout':1,'description':'changed','version':2}
        for field,value in changes.items():
            with self.subTest(field=field):
                other=deepcopy(d);other[field]=value
                with self.assertRaisesRegex(ValueError,'Conflicting tool definition'):
                    self.app.tools.register(other)
                self.assertEqual(self.app.tools.health('registered')['definition'],d)

    def test_registered_definition_is_protected_from_external_mutation(self):
        d=definition('protected','echo','testing',['retrieve'])
        expected=deepcopy(d);self.app.tools.register(d)
        d['permission_requirements'].clear();d['input_schema']['required'].clear()
        self.app.tools.health('protected')['definition']['permission_requirements'].clear()
        self.app.tools.discover()[0]['definition']['capabilities'].clear()
        self.app.tools.definitions['protected']['output_schema'].clear()
        self.assertEqual(self.app.tools.health('protected')['definition'],expected)

    def test_exporter_registration_can_repeat_before_production(self):
        self.populate()
        register_exporter(self.app)
        expected=self.app.tools.health('export.draft')['definition']
        register_exporter(self.app)
        self.assertEqual(self.app.tools.health('export.draft')['definition'],expected)
        request=obj('repeat-export',goal='Synthetic',project={},domains=[],populations=[],
            scenarios=[],outputs=['markdown','html','json'],evidence={},
            available_tools=['export.draft'],restrictions={})
        run=produce(self.app,request,'repeat-export')
        self.assertEqual(run['state'],'draft_ready',run.get('error'))
        self.assertEqual(len(run['steps']['artifacts']),3)

    def test_taxonomy_cycles_and_unreviewed_relations(self):
        self.populate()
        self.app.save_object('TaxonomyTerm',obj('child',dimension_id='domain',label='child',parents=['alpha'],status='active'))
        with self.assertRaises(ValueError):self.app.save_object('TaxonomyTerm',obj('alpha',dimension_id='domain',label='alpha',parents=['child'],status='active'))
        with self.assertRaises(ValueError):self.app.save_object('KnowledgeRelation',obj('bad',subject='K-SYN',predicate='supports',object='alpha',review_state='approved',evidence_references=[]))

    def test_negative_schema_dependency_and_path_controls(self):
        self.app.create_fixture('.json','PROBE')
        d=definition('missing','echo','test',dependencies=['nonexistent_v03_dependency_xyz'])
        self.app.tools.register(d)
        self.assertEqual(self.app.tools.health('missing')['status'],'unavailable_dependency')
        d=definition('wrong-output','echo','test');d['output_schema']={'type':'string'}
        self.app.tools.register(d)
        result=self.app.tools.invoke('wrong-output',{'value':1},{},'wrong-output',source_ids=['PROBE'])
        self.assertEqual(result['state'],'failed');self.assertIn('expected string',result['error'])
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
        self.assertEqual(run['state'],'draft_ready',run.get('error'))
        for name in run['steps']['artifacts']:self.assertTrue(self.app.artifact(run['id'],name).exists())
        self.assertEqual(produce(self.app,request,'production')['id'],run['id'])
        self.assertEqual(len(run['steps']['artifact_contracts']),3)

    def test_exporter_rejects_forged_snapshot_and_revoked_sources(self):
        self.populate()
        request=obj('export-policy',goal='Synthetic',project={},domains=[],populations=[],
            scenarios=[],outputs=['markdown','html','json'],evidence={},available_tools=['export.draft'],restrictions={})
        run=produce(self.app,request,'export-policy')
        current=self.app.get_run(run['id']);forged=deepcopy(current)
        forged['steps']['evidence']['entries'][0]['summary']='Unregistered caller content'
        def invoke(snapshot,key):
            return self.app.tools.invoke('export.draft',{'root':str(self.root),
                'config':'config/runtime.json','run':snapshot},{'retrieve':True,'adapt':True},key)
        result=invoke(forged,'forged-export')
        self.assertEqual(result['state'],'failed');self.assertIn('binding',result['error'])
        self.app.withdraw('SYN','withdraw export source')
        result=invoke(current,'revoked-export')
        self.assertEqual(result['state'],'failed');self.assertIn('not current',result['error'])

    def test_retrieve_and_adapt_are_not_inferred_from_parse(self):
        manifest,unit=self.populate()
        for retrieve,adapt in ((True,False),(False,True),(False,False)):
            with self.subTest(retrieve=retrieve,adapt=adapt):
                body=deepcopy(self.app.asset('SYN')['body'])
                body['rights'].update(internal_parse=True,retrieve=retrieve,adapt=adapt)
                revision=self.app.put('asset','SYN',body,'admitted')
                unit['evidence_references'][0]['source_revision']=revision
                self.app.confirm(unit,True)
                self.assertEqual(bool(self.app.retrieve()['entries']),retrieve)
                self.assertEqual(self.app.retrieve(purpose='adapt')['entries'],[])

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

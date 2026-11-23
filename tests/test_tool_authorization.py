from copy import deepcopy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from pipeline import ROOT, RIGHTS
from platform_store import Platform
from platform_tools import ToolRegistry, definition, echo


class ToolAuthorizationChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'config').mkdir();(self.root/'data/raw').mkdir(parents=True)
        for name in ('runtime.json','domain_rules.json'):shutil.copy(ROOT/'config'/name,self.root/'config'/name)
        self.app=Platform(self.root)

    def tearDown(self):
        self.app.db.close();self.temp.cleanup()

    def payload(self,id_):
        a=self.app.asset(id_)
        return {'root':str(self.root),'raw_directory':'data/raw','path':a['body']['path'],
            'source':id_,'revision':self.app._extraction_revision(a)}

    def denied(self,result,reason):
        self.assertEqual(result['state'],'failed',result)
        self.assertIn(reason,result['error'])
        self.assertEqual(result['output'],{})

    def test_direct_parser_rejects_real_and_unknown_sources(self):
        path=self.root/'data/raw/untrusted.txt';path.write_text('Untrusted input',encoding='utf-8')
        for id_,synthetic,sensitive in [('REAL',False,'none'),('UNKNOWN',True,'unknown'),('CLAIM',True,'none')]:
            with self.subTest(id=id_):
                self.app.put('asset',id_,{'path':'data/raw/untrusted.txt','synthetic':synthetic,
                    'sensitive':sensitive,'rights':{r:True for r in RIGHTS}},'admitted')
                self.denied(self.app.tools.invoke('parse.txt',self.payload(id_),
                    {r:True for r in RIGHTS},'direct-'+id_),'PermissionError')

    def test_manifest_flag_does_not_admit_an_unregistered_file(self):
        (self.root/'data/raw/untrusted.txt').write_text('Untrusted input',encoding='utf-8')
        manifest={'id':'FAKE','title':'Claim','path':'data/raw/untrusted.txt','synthetic':True,
            'sensitive':'none','provenance':'program-generated','rights':{r:True for r in RIGHTS}}
        with self.assertRaises((ValueError,PermissionError)):self.app.extract(manifest,'fake-manifest')
        self.assertEqual(self.app.records('asset'),[])

    def test_direct_parser_uses_record_permissions_not_caller_grants(self):
        manifest=self.app.create_fixture('.txt','SYN')
        body=deepcopy(self.app.asset('SYN')['body']);body['rights']['internal_parse']=False
        self.app.put('asset','SYN',body,'admitted')
        self.denied(self.app.tools.invoke('parse.txt',self.payload('SYN'),
            {'internal_parse':True},'missing-parse'),'internal_parse')
        with self.assertRaises((ValueError,PermissionError)):self.app.extract(manifest,'forged-rights')

    def test_ocr_asr_and_external_rights_are_independent(self):
        self.app.create_fixture('.json','SYN')
        for permission in ('ocr','asr','external_processing','external_model_processing','model_training'):
            with self.subTest(permission=permission):
                self.app.tools.register(definition('needs-'+permission,'echo','probe',[permission]))
                self.denied(self.app.tools.invoke('needs-'+permission,{'value':1},
                    {'internal_parse':True,permission:True},permission,source_ids=['SYN']),permission)

    def test_unknown_tool_sensitivity_and_unbound_payload_are_denied(self):
        self.app.create_fixture('.json','SYN')
        d=definition('unknown-policy','echo','probe');d['data_sensitivity']='unknown'
        self.app.tools.register(d)
        self.denied(self.app.tools.invoke('unknown-policy',{'value':1},{},'unknown-policy',source_ids=['SYN']),'sensitivity')
        self.app.tools.register(definition('echo','echo','probe'))
        self.denied(self.app.tools.invoke('echo',{'value':'unregistered content'},{},'unbound',source_ids=['SYN']),'binding')
        self.denied(self.app.tools.invoke('echo',{'value':1},{},'no-sources'),'source')

    def test_file_hash_path_revision_and_source_state_are_bound(self):
        self.app.create_fixture('.txt','SYN')
        original=self.payload('SYN')
        other=self.root/'data/raw/other.txt';other.write_text('Other input',encoding='utf-8')
        for field,value in [('path','data/raw/other.txt'),('revision',99),('root',str(self.root/'other'))]:
            with self.subTest(field=field):
                payload=dict(original);payload[field]=value
                self.denied(self.app.tools.invoke('parse.txt',payload,{'internal_parse':True},'bad-'+field),'binding')
        (self.root/original['path']).write_text('Changed after generation',encoding='utf-8')
        self.denied(self.app.tools.invoke('parse.txt',original,{'internal_parse':True},'changed-file'),'hash')

    def test_cached_invocation_cannot_bypass_revocation(self):
        self.app.create_fixture('.txt','SYN');payload=self.payload('SYN')
        result=self.app.tools.invoke('parse.txt',payload,{'internal_parse':True},'cached')
        self.assertEqual(result['state'],'completed')
        self.app.withdraw('SYN','revoked')
        self.denied(self.app.tools.invoke('parse.txt',payload,{'internal_parse':True},'cached'),'admitted')

    def test_record_sensitivity_is_rechecked_even_with_generation_receipt(self):
        self.app.create_fixture('.txt','SYN')
        for sensitive in ('unknown','personal','minor_health',None):
            with self.subTest(sensitive=sensitive):
                body=deepcopy(self.app.asset('SYN')['body']);body['sensitive']=sensitive
                self.app.put('asset','SYN',body,'admitted')
                self.denied(self.app.tools.invoke('parse.txt',self.payload('SYN'),
                    {'internal_parse':True},'sensitive-'+str(sensitive)),'sensitivity')

    def test_cached_probe_rechecks_current_source_permissions(self):
        self.app.create_fixture('.json','SYN')
        self.app.tools.register(definition('probe','echo','probe',['retrieve']))
        def invoke():return self.app.tools.invoke('probe',{'value':1},{'retrieve':True},'cached-probe',source_ids=['SYN'])
        self.assertEqual(invoke()['state'],'completed')
        body=deepcopy(self.app.asset('SYN')['body']);body['rights']['retrieve']=False
        self.app.put('asset','SYN',body,'admitted')
        self.denied(invoke(),'retrieve')

    def test_registry_without_authorizer_and_new_unbound_adapter_fail_closed(self):
        tools=ToolRegistry(self.app.db);tools.register(definition('untrusted','echo','probe'))
        self.denied(tools.invoke('untrusted',{'value':1},{},'no-authorizer'),'authorizer')
        self.app.create_fixture('.json','SYN')
        self.app.tools.register_adapter('unbound',echo)
        self.app.tools.register(definition('unbound','unbound','probe'))
        self.denied(self.app.tools.invoke('unbound',{'value':1},{},'unbound-adapter',source_ids=['SYN']),'binding')

    def test_generated_origin_survives_restart_and_repeated_extraction(self):
        manifest=self.app.create_fixture('.txt','SYN')
        self.app.db.close();self.app=Platform(self.root)
        first=self.app.extract(manifest,'stable-extraction')
        self.assertEqual(first['state'],'candidate')
        self.assertEqual(self.app.extract(manifest,'stable-extraction'),first)
        self.assertEqual(self.app.asset('SYN')['revision'],first['source_revision'])


if __name__=='__main__':unittest.main()

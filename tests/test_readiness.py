"""Synthetic unit, integration, abnormal-state and compatibility acceptance."""
from importlib.util import find_spec
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from platform_migrate import rollback
from readiness_agent import artifact, produce, publish, review, request_contract
from readiness_demo_data import evaluate, grant, isolate, seed, GRANT
from readiness_media import ParserRegistry, Unavailable, check_archive
from readiness_model import SyntheticModel
from readiness_parse import correct_span, parse, span_ref
from readiness_retrieval import search
from readiness_store import Readiness, sha256


class BrokenModel(SyntheticModel):
    def plan(self, request, evidence):
        raise RuntimeError('provider unavailable')


class UnsafeModel(SyntheticModel):
    def plan(self, request, evidence):
        return dict(super().plan(request,evidence),tools=['shell'])


class SlowModel(SyntheticModel):
    def plan(self, request, evidence):
        time.sleep(2)
        return super().plan(request,evidence)


def broken_parser(*args):
    raise Unavailable('generated missing dependency')


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=isolate(Path(self.tmp.name)/'workspace')
        self.app=Readiness(self.root)
        self.addCleanup(self.app.db.close)

    def source(self,id_='s',ext='.txt',scopes=('retrieve','adapt')):
        self.app.generate_source(id_,ext,'Synthetic '+id_)
        grant(self.app,id_,scopes)
        return self.app.record('ReadinessSource',id_)

    def candidate(self,id_='x',source='s'):
        self.source(source)
        extraction=parse(self.app,source,'parse-'+source)
        part=extraction['parts'][0]
        unit=dict(id=id_,title='Synthetic '+id_,statement='Fixture only',
                  facets={'learner':['coach'],'training_population':['youth'],'domain':['football']},
                  objectives=[],prerequisites=[],risks=[],references=[span_ref(extraction['id'],part,part['segments'][0])])
        self.app.save_unit(unit)
        return unit

    def ready(self):
        self.candidate('x')
        self.app.review_unit('x','operator','approved','Synthetic verification')
        return {'track':'football','query':'x'}

    def test_pending_then_explicit_grant(self):
        row=self.app.generate_source('s','.txt')
        self.assertEqual(self.app.source_status(row),'pending_review')
        with self.assertRaises(PermissionError):
            self.app.authorize('s','internal_parse')
        grant(self.app,'s',('retrieve',))
        self.assertEqual(self.app.authorize('s','retrieve')['id'],'s')
        with self.assertRaises(PermissionError):
            self.app.authorize('s','adapt')
        self.assertTrue(self.app.record('SourceAuthorization','s')['body']['proof'])

    def test_independent_scopes(self):
        self.source(scopes=('ocr',))
        self.app.authorize('s','internal_parse','ocr')
        for permission in ('asr','video_processing','external_model_processing','redistribute','model_training'):
            with self.subTest(permission=permission),self.assertRaises(PermissionError):
                self.app.authorize('s',permission)

    def test_revoke_and_replace(self):
        first=self.source()
        parse(self.app,'s','parse')
        grant(self.app,'s',(),state='revoked')
        with self.assertRaises(PermissionError):
            parse(self.app,'s','parse')
        new=self.app.generate_source('s','.txt','Synthetic replacement')
        self.assertNotEqual(first['body']['path'],new['body']['path'])
        self.assertEqual(new['body']['version'],2)
        self.assertEqual(self.app.source_status(new),'pending_review')
        grant(self.app,'s',('retrieve',))
        with self.assertRaises(ValueError):
            parse(self.app,'s','parse')

    def test_quarantine_expiry_and_rejection(self):
        source=self.source()
        for state in ('expired','quarantined','rejected'):
            grant(self.app,'s',(),state=state)
            self.assertEqual(self.app.source_status(source),state)
            with self.assertRaises(PermissionError):
                self.app.authorize('s','retrieve')
        with self.assertRaises(ValueError):
            self.app.record_grant('s',reviewer='operator',proof='synthetic',
                                  expires_at='2000-01-01T00:00:00+00:00',scopes={})

    def test_forgery_and_duplicate(self):
        self.source()
        with self.assertRaises(ValueError):
            self.app.generate_source('duplicate','.txt','Synthetic s')
        with self.assertRaises(PermissionError):
            self.app.authorize('missing','retrieve')
        with self.assertRaises(ValueError):
            self.app.record_grant('s',**GRANT,scopes={'retrieve':'yes'})
        with self.assertRaises(ValueError):
            self.app.path({'path':'../other.txt'})

    def test_file_changed_rejects_cached_parse_and_export(self):
        request=self.ready()
        draft=produce(self.app,request,'produce')
        original=self.app.path(self.app.record('ReadinessSource','s')['body'])
        original.write_text('Synthetic changed',encoding='utf8')
        with self.assertRaises(PermissionError):
            parse(self.app,'s','parse-s')
        with self.assertRaises(PermissionError):
            artifact(self.app,draft['id'],'lesson.json')
        self.assertFalse(search(self.app,'x')['entries'])

    def test_span_correction_retains_machine(self):
        unit=self.candidate()
        ref=unit['references'][0]
        edited=correct_span(self.app,'correction',ref,'Synthetic corrected','operator','Clarify')
        self.assertNotEqual(self.app.evidence_span(ref)['extracted_content'],self.app.evidence_span(edited)['extracted_content'])
        with self.assertRaises(ValueError):
            self.app.evidence_span(dict(ref,locator={'bogus':1}))

    def test_parse_error_recovery_and_scope(self):
        self.source()
        registry=ParserRegistry()
        registry.register('.txt','second',broken_parser)
        with self.assertRaises(Unavailable):
            parse(self.app,'s','try',('native','second'),registry=registry)
        saved=self.app.record('ParseRun','try')
        self.assertEqual(len(saved['body']['completed']),1)
        with self.assertRaises(ValueError):
            parse(self.app,'s','try',('native','second'),registry=registry)
        adapter,_=registry.route('.txt','native')
        registry.register('.txt','second',adapter)
        recovered=parse(self.app,'s','try',('native','second'),retry=True,registry=registry)
        self.assertEqual(recovered['completed'][0],saved['body']['completed'][0])
        self.assertEqual(len(recovered['parts']),2)

    def test_archive_rejects_invalid_members(self):
        zip_=self.root/'bad.docx'
        with zipfile.ZipFile(zip_,'w') as archive:
            archive.writestr('../path','Synthetic')
        with self.assertRaises(ValueError):
            check_archive(zip_)

    def test_unit_lifecycle_and_merge(self):
        a=self.candidate('a','s')
        self.candidate('b','t')
        self.assertFalse(search(self.app,'a')['entries'])
        self.assertEqual(len(search(self.app,'a',states=('review_pending',))['entries']),1)
        merged=self.app.merge_units(dict(a,id='c'),['a','b'],'operator','Synthetic dedupe')
        self.assertEqual(len(merged['body']['references']),2)
        self.assertEqual(self.app.record('ReadinessUnit','a')['state'],'withdrawn')
        self.app.review_unit('c','operator','approved','Synthetic approval')
        self.assertEqual(search(self.app)['entries'][0]['knowledge']['id'],'c')

    def test_36_query_benchmark(self):
        seed(self.app)
        report=evaluate(self.app)
        self.assertGreaterEqual(report['queries'],30)
        self.assertEqual((report['recall'],report['hit'],report['mrr']),(1,1,1))
        self.assertEqual((report['unauthorized'],report['false_retrievals']),(0,0))
        self.assertTrue(next(x for x in report['rows'] if x['group']=='conflict')['conflict_detected'])

    def test_reranker_cannot_add_hidden_id(self):
        self.ready()
        with self.assertRaises(ValueError):
            search(self.app,'x',rerank=lambda *args:['denied'])

    def test_invalid_user_request(self):
        with self.assertRaises(ValueError):
            request_contract({'track':'football','query':'x','roles':['reviewer']})
        with self.assertRaises(ValueError):
            request_contract({'track':'football','query':'x','timeline':[{'asset':'../a','start_ms':0,'end_ms':10}]})

    def test_production_outputs_and_review(self):
        request=self.ready()
        draft=produce(self.app,request,'run')
        self.assertEqual(draft['state'],'draft_ready',draft['error'])
        self.assertEqual(produce(self.app,request,'run')['id'],draft['id'])
        with self.assertRaises(PermissionError):
            publish(self.app,draft['id'])
        with self.assertRaises(ValueError):
            produce(self.app,dict(request,query='other'),'run')
        for name,entry in draft['steps']['artifacts'].items():
            self.assertEqual(sha256(artifact(self.app,draft['id'],name)),entry['sha256'])
        self.assertIn('role="status"',artifact(self.app,draft['id'],'activity.html').read_text('utf8'))
        approved=review(self.app,draft['id'],reviewer='operator',decision='approved',note='Synthetic verification')
        self.assertEqual(approved['state'],'approved')
        with self.assertRaises(PermissionError):
            publish(self.app,draft['id'],public=True)
        self.assertEqual(publish(self.app,draft['id'])['state'],'published')

    def test_model_invalid_output_timeout_and_recovery(self):
        request=self.ready()
        for key,provider,expected in [('broken',BrokenModel(),'unavailable'),('unsafe',UnsafeModel(),'authorized'),
                                     ('slow',SlowModel(),'timed out')]:
            failed=produce(self.app,request,key,provider=provider,timeout=.1 if key=='slow' else 5)
            self.assertEqual(failed['state'],'failed',failed['error'])
            self.assertIn(expected,failed['error'])
            with self.assertRaises(ValueError):
                produce(self.app,request,key)
            resumed=produce(self.app,request,key,retry=True)
            self.assertEqual((resumed['id'],resumed['state']),(failed['id'],'draft_ready'))

    def test_untrusted_text_is_escaped(self):
        unit=self.candidate()
        self.app.save_unit(dict(unit,statement='Synthetic <script>change_roles()</script>'))
        self.app.review_unit('x','operator','approved','Synthetic verification')
        run=produce(self.app,{'track':'football','query':'x'},'escape')
        data=artifact(self.app,run['id'],'lesson.html').read_text('utf8')
        self.assertNotIn('<script>change_roles()',data)
        self.assertIn('&lt;script&gt;',data)

    def test_revoke_invalidates_download(self):
        run=produce(self.app,self.ready(),'run')
        grant(self.app,'s',(),state='revoked')
        with self.assertRaises(PermissionError):
            artifact(self.app,run['id'],'lesson.html')

    def stage(self,name):
        raw=self.root/self.app.config['raw_directory']/'staging'
        raw.mkdir(parents=True,exist_ok=True)
        return raw/name

    def stage_metadata(self,source_type):
        return dict(title='Synthetic staged manifest',provider='Generated test',source_type=source_type,
                    owner='operator',sensitivity='none',created_at='2026-01-01T00:00:00+00:00')

    def test_staged_operator_interface_disabled_by_default(self):
        staged=self.stage('synthetic.txt')
        staged.write_text('Synthetic operator staging verification',encoding='utf8')
        meta=self.stage_metadata('txt')
        with self.assertRaises(PermissionError):
            self.app.register_local_source('staged',staged.name,meta)
        with Readiness(self.root,authorized_local_intake=True) as operator:
            registered=operator.register_local_source('staged',staged.name,meta)
            self.assertEqual(operator.source_status(registered),'pending_review')
            self.assertNotEqual(operator.path(registered['body']),staged)
            self.assertEqual(sha256(operator.path(registered['body'])),sha256(staged))
            grant(operator,'staged',('retrieve',))
            self.assertIn('Synthetic',parse(operator,'staged','staged-parse')['parts'][0]['segments'][0]['extracted_content'])
        with self.assertRaises(PermissionError):
            self.app.authorize('staged','retrieve')

    @unittest.skipUnless(find_spec('pypdf'),'PDF chunk tests run in the optional job')
    def test_pdf_page_checkpoints_and_exact_locator(self):
        from pypdf import PdfWriter
        staged=self.stage('synthetic.pdf')
        writer=PdfWriter()
        for i in range(41):
            writer.add_blank_page(width=100+i,height=80)
        writer.write(staged)
        with Readiness(self.root,authorized_local_intake=True) as operator:
            operator.register_local_source('staged',staged.name,self.stage_metadata('pdf'))
            grant(operator,'staged',('retrieve',))
            from unittest.mock import patch
            from readiness_media import pdf_pages
            def interrupt(path,start,end):
                if start>=20:
                    raise ValueError('Synthetic page processing interruption')
                return pdf_pages(path,start,end)
            with patch('readiness_parse.pdf_pages',side_effect=interrupt):
                with self.assertRaises(ValueError):
                    parse(operator,'staged','document')
            self.assertEqual(len(operator.records('ParseChunk')),1)
            result=parse(operator,'staged','document',retry=True)
            self.assertEqual(len(operator.records('ParseChunk')),3)
            self.assertEqual([s['locator']['page'] for s in result['parts'][0]['segments']],list(range(1,42)))
            self.assertEqual(result['parts'][0]['segments'][-1]['locator']['media_box'],[0,0,140,80])

    def test_backup_restore_isolated(self):
        self.source()
        backup=self.app.backup()
        path = Path(backup['path'])
        self.assertTrue(path.is_file())
        target=self.root/'restore.sqlite3'
        rollback(path,target)
        self.assertTrue(target.is_file())
        with self.assertRaises(ValueError):
            rollback(path,target)

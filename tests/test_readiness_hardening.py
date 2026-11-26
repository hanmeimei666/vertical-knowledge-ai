"""Regression tests for pre-content architecture hardening, adapters, guardrails, impact analysis and CLI."""
from contextlib import closing
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.util import find_spec
import json
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Thread
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import readiness_cli
from readiness_agent import artifact, produce
from readiness_demo import Workbench, serve
from readiness_demo_data import EXPIRY, grant, isolate
from readiness_eval import RichBlueprintModel, run_eval
from readiness_media import ParserRegistry, Unavailable, chunk_segments, generate_silent_video, probe_media
from readiness_model import LocalOllamaModel, OpenAICompatibleModel, extract_json_object, validate_plan
from readiness_parse import parse, span_ref
from readiness_retrieval import OllamaEmbeddingProvider, OpenAICompatibleEmbeddingProvider, SyntheticEmbedding, VectorSearch, search
from readiness_store import Readiness


def _start_mock_server(handler_cls):
    server = HTTPServer(('127.0.0.1', 0), handler_cls)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f'http://127.0.0.1:{server.server_port}'


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = isolate(Path(self.tmp.name) / 'workspace')
        self.app = Readiness(self.root, authorized_local_intake=True)
        self.addCleanup(self.app.db.close)

    def _stage_file(self, name, content, sensitivity='internal'):
        staging = self.root / self.app.config['raw_directory'] / 'staging'
        staging.mkdir(parents=True, exist_ok=True)
        path = staging / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding='utf8')
        return {
            'title': 'Synthetic ' + name,
            'provider': 'Generated test',
            'source_type': path.suffix.lstrip('.').lower(),
            'owner': 'operator',
            'sensitivity': sensitivity,
            'created_at': '2026-01-01T00:00:00+00:00',
        }

    def test_sensitivity_policy_matrix(self):
        meta_internal = self._stage_file('internal.txt', 'Synthetic internal note', sensitivity='internal')
        self.app.register_local_source('src-int', 'internal.txt', meta_internal)
        with self.assertRaises(PermissionError):
            self.app.record_grant('src-int', reviewer='op', proof='p', expires_at=EXPIRY,
                                  scopes={'internal_parse': True, 'external_model_processing': True})
        with self.assertRaises(PermissionError):
            self.app.record_grant('src-int', reviewer='op', proof='p', expires_at=EXPIRY,
                                  scopes={'internal_parse': True, 'redistribute': True})
        self.app.record_grant('src-int', reviewer='op', proof='p', expires_at=EXPIRY,
                              scopes={'internal_parse': True, 'retrieve': True, 'adapt': True})
        self.assertEqual(self.app.authorize('src-int', 'retrieve', 'adapt')['id'], 'src-int')

        meta_restricted = self._stage_file('secret.txt', 'Synthetic restricted note', sensitivity='restricted')
        self.app.register_local_source('src-sec', 'secret.txt', meta_restricted)
        with self.assertRaises(PermissionError):
            self.app.record_grant('src-sec', reviewer='op', proof='p', expires_at=EXPIRY,
                                  scopes={'internal_parse': True})

    def test_verification_cache_deduplicates_hashes_and_still_catches_tampering(self):
        self.app.generate_source('s1', '.txt', 'Synthetic cache source')
        grant(self.app, 's1', ('retrieve', 'adapt'))
        ex = parse(self.app, 's1', 'p1')
        part = ex['parts'][0]
        ref = span_ref(ex['id'], part, part['segments'][0])
        for i in range(15):
            self.app.draft_unit_from_span(
                f'u-{i}', ref, title=f'Synthetic unit {i}',
                statement=f'Agility drill {i} shared-token',
                facets={'domain': ['conditioning'], 'learner': ['educator'], 'training_population': ['youth']},
            )
            self.app.review_unit(f'u-{i}', 'op', 'approved', 'ok')

        before = self.app._hash_checks
        res = search(self.app, 'shared-token', {'domain': ['conditioning']}, limit=20)
        self.assertEqual(len(res['entries']), 15)
        self.assertEqual(self.app._hash_checks - before, 1)

        # File tampering must be caught immediately on the next search.
        path = self.app.path(self.app.record('ReadinessSource', 's1')['body'])
        path.write_text('Tampered content', encoding='utf8')
        self.assertEqual(search(self.app, 'shared-token', {'domain': ['conditioning']})['entries'], [])

    def test_persistent_vector_cache_and_fts5(self):
        self.app.generate_source('s-zh', '.txt', '青少年体能折返跑基础训练')
        grant(self.app, 's-zh', ('retrieve', 'adapt'))
        ex = parse(self.app, 's-zh', 'p-zh')
        part = ex['parts'][0]
        ref = span_ref(ex['id'], part, part['segments'][0])
        self.app.draft_unit_from_span(
            'u-zh', ref, title='青少年灵敏折返跑',
            statement='动态热身后进行低强度折返跑节奏练习',
            facets={'domain': ['conditioning'], 'learner': ['educator'], 'training_population': ['youth']},
        )
        self.app.review_unit('u-zh', 'op', 'approved', 'ok')

        self.assertEqual(len(search(self.app, '折返跑')['entries']), 1)
        vs = VectorSearch(SyntheticEmbedding())
        search(self.app, '折返跑', method='hybrid', vectors=vs)
        self.assertEqual(vs.cache_misses, 1)
        self.assertEqual(vs.cache_hits, 0)
        search(self.app, '折返跑', method='hybrid', vectors=vs)
        self.assertEqual(vs.cache_hits, 1)

    def test_reverse_impact_analysis_and_cascade_invalidation(self):
        self.app.generate_source('s-imp', '.txt', 'Synthetic impact source')
        grant(self.app, 's-imp', ('retrieve', 'adapt'))
        ex = parse(self.app, 's-imp', 'p-imp')
        part = ex['parts'][0]
        ref = span_ref(ex['id'], part, part['segments'][0])
        self.app.draft_unit_from_span(
            'u-imp', ref, title='Impact target unit', statement='Target evidence',
            facets={'domain': ['football'], 'learner': ['coach'], 'training_population': ['youth']},
        )
        self.app.review_unit('u-imp', 'op', 'approved', 'ok')
        run = produce(self.app, {'track': 'football', 'query': 'Impact'}, 'imp-run')
        self.assertEqual(run['state'], 'draft_ready')

        report = self.app.impact_analysis(source_id='s-imp')
        self.assertEqual(report['parse_runs'], ['p-imp'])
        self.assertEqual([u['id'] for u in report['direct_units']], ['u-imp'])
        self.assertEqual([r['id'] for r in report['runs']], [run['id']])

        cascade = self.app.invalidate_impacted_runs(source_id='s-imp', reviewer='op', note='Source revised')
        self.assertEqual(cascade['invalidated_runs'], [run['id']])
        self.assertEqual(self.app.get_run(run['id'], check=False)['state'], 'invalidated')
        with self.assertRaises(PermissionError):
            artifact(self.app, run['id'], 'lesson.html')

    def test_domain_rule_refusal_and_enriched_blueprint(self):
        self.app.generate_source('s-rich', '.txt', 'Synthetic football passing structure')
        grant(self.app, 's-rich', ('retrieve', 'adapt'))
        ex = parse(self.app, 's-rich', 'p-rich')
        part = ex['parts'][0]
        ref = span_ref(ex['id'], part, part['segments'][0])
        self.app.draft_unit_from_span(
            'u-rich', ref, title='Passing rhythm', statement='Structured short passing drill',
            facets={'domain': ['football'], 'learner': ['coach'], 'training_population': ['youth']},
        )
        self.app.review_unit('u-rich', 'op', 'approved', 'ok')

        blocked = produce(self.app, {'track': 'football', 'query': '请诊断我的膝盖并开具处方'}, 'blocked-rule')
        self.assertEqual(blocked['state'], 'blocked')
        self.assertIn('excluded_domain_request', blocked['error'])

        rich = produce(self.app, {'track': 'football', 'query': 'Passing'}, 'rich-run', provider=RichBlueprintModel())
        self.assertEqual(rich['state'], 'draft_ready', rich.get('error'))
        html = artifact(self.app, rich['id'], 'activity.html').read_text('utf8')
        self.assertIn('安全边界与使用限制', html)
        self.assertIn('热身与准备', html)
        self.assertIn('single_choice', html)
        self.assertIn('关键结论溯源', html)

        # Reject hallucinated citations not in selected units.
        bad_plan = RichBlueprintModel().plan({'track': 'football'}, rich['steps']['evidence'])
        bad_plan['citations'] = [{'claim': 'fake', 'unit_id': 'nonexistent'}]
        with self.assertRaises(ValueError):
            validate_plan(bad_plan, rich['steps']['evidence'], {'documents', 'interactive'})

    def test_real_model_and_embedding_adapters_with_mock_http(self):
        self.app.generate_source('s-llm', '.txt', 'Synthetic ollama adapter test')
        grant(self.app, 's-llm', ('retrieve', 'adapt'))
        ex = parse(self.app, 's-llm', 'p-llm')
        part = ex['parts'][0]
        ref = span_ref(ex['id'], part, part['segments'][0])
        self.app.draft_unit_from_span(
            'u-llm', ref, title='Ollama unit', statement='Fixture text for local adapter',
            facets={'domain': ['football'], 'learner': ['coach'], 'training_population': ['youth']},
        )
        self.app.review_unit('u-llm', 'op', 'approved', 'ok')

        class MockAIHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                length = int(self.headers.get('Content-Length', 0))
                req_body = json.loads(self.rfile.read(length).decode('utf8'))
                if self.path == '/api/embeddings':
                    resp = {'embedding': [1.0, 0.5, 0.25]}
                elif self.path == '/v1/embeddings':
                    resp = {'data': [{'embedding': [1.0, 0.5, 0.25]}]}
                elif self.path == '/api/chat':
                    plan_json = json.dumps({
                        'units': ['u-llm'],
                        'objectives': ['Verify local Ollama contract'],
                        'tools': ['documents', 'interactive'],
                        'interaction': {'prompt': 'Trace field?', 'answer': 'locator', 'feedback': 'Correct.'},
                    })
                    resp = {'message': {'content': '```json\n' + plan_json + '\n```'}}
                elif self.path == '/v1/chat/completions':
                    plan_json = json.dumps({
                        'units': ['u-llm'],
                        'objectives': ['Verify OpenAI-compatible contract'],
                        'tools': ['documents', 'interactive'],
                        'interaction': {'prompt': 'Trace field?', 'answer': 'locator', 'feedback': 'Correct.'},
                    })
                    resp = {'choices': [{'message': {'content': plan_json}}]}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(resp).encode('utf8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server, thread, base = _start_mock_server(MockAIHandler)
        try:
            ollama_emb = OllamaEmbeddingProvider(model='bge-m3', endpoint=base + '/api/embeddings')
            res_emb = search(self.app, 'Ollama', method='semantic', vectors=VectorSearch(ollama_emb))
            self.assertEqual([e['knowledge']['id'] for e in res_emb['entries']], ['u-llm'])

            oai_emb = OpenAICompatibleEmbeddingProvider(model='text-emb', endpoint=base + '/v1/embeddings')
            self.assertEqual(len(oai_emb.embed('test')), 3)

            run_ollama = produce(
                self.app, {'track': 'football', 'query': 'Ollama'}, 'ollama-run',
                provider=LocalOllamaModel(model='qwen2.5:7b', endpoint=base + '/api/chat'),
            )
            self.assertEqual(run_ollama['state'], 'draft_ready', run_ollama.get('error'))
            self.assertEqual(run_ollama['steps']['blueprint']['model_provider'], 'LocalOllamaModel')

            run_oai = produce(
                self.app, {'track': 'football', 'query': 'Ollama'}, 'oai-run',
                provider=OpenAICompatibleModel(model='local-vllm', endpoint=base + '/v1/chat/completions'),
            )
            self.assertEqual(run_oai['state'], 'draft_ready', run_oai.get('error'))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        # Non-loopback external endpoints must be rejected unless allow_external=True.
        with self.assertRaises(PermissionError):
            OpenAICompatibleModel(model='gpt', endpoint='https://api.example.invalid/v1/chat/completions')
        # And even with allow_external=True, source without external_model_processing fails permission gate.
        ext_provider = OpenAICompatibleModel(
            model='gpt', endpoint='https://api.example.invalid/v1/chat/completions', allow_external=True
        )
        denied = produce(self.app, {'track': 'football', 'query': 'Ollama'}, 'ext-denied', provider=ext_provider)
        self.assertEqual(denied['state'], 'failed')
        self.assertIn('PermissionError', denied['error'])
        self.assertEqual(extract_json_object('prefix {"a": 1} suffix'), {'a': 1})

    def test_document_chunking_and_whisper_route(self):
        long_text = '\n'.join(f'Line {i}: ' + ('synthetic sports drill ' * 12) for i in range(15))
        meta = self._stage_file('long.txt', long_text, sensitivity='none')
        self.app.register_local_source('src-long', 'long.txt', meta)
        grant(self.app, 'src-long', ('retrieve', 'adapt'))
        ex = parse(self.app, 'src-long', 'p-long', stages=('native', 'chunk'))
        self.assertEqual(len(ex['parts']), 2)
        chunks = ex['parts'][1]['segments']
        self.assertGreater(len(chunks), 2)
        self.assertEqual(chunks[0]['locator']['type'], 'chunk')
        self.assertIn('start_locator', chunks[0]['locator'])
        self.assertEqual(chunk_segments([]), [])

        reg = ParserRegistry()
        adapter, perms = reg.route('.mp4', 'whisper')
        self.assertEqual(set(perms), {'asr', 'video_processing'})
        if find_spec('faster_whisper') is None and shutil.which('whisper') is None:
            with self.assertRaises(Unavailable):
                adapter(Path('unused.mp4'), 's', 1, Path(self.tmp.name))

    @unittest.skipUnless(find_spec('imageio_ffmpeg') or shutil.which('ffmpeg'), 'FFmpeg required for silent video test')
    def test_silent_video_tracks_and_concat(self):
        staging = self.root / self.app.config['raw_directory'] / 'staging'
        staging.mkdir(parents=True, exist_ok=True)
        silent_path = staging / 'silent.mp4'
        generate_silent_video(silent_path, duration_s=0.6)
        meta = {
            'title': 'Synthetic silent drill', 'provider': 'Generated test', 'source_type': 'mp4',
            'owner': 'operator', 'sensitivity': 'none', 'created_at': '2026-01-01T00:00:00+00:00',
        }
        self.app.register_local_source('src-silent', 'silent.mp4', meta)
        grant(self.app, 'src-silent', ('retrieve', 'adapt', 'video_processing'))
        ex = parse(self.app, 'src-silent', 'p-silent', stages=('native', 'tracks'))
        self.assertFalse(probe_media(self.app.path(self.app.record('ReadinessSource', 'src-silent')['body']))['has_audio'])
        tracks_part = ex['parts'][1]
        self.assertEqual(len(tracks_part['segments']), 1)  # Keyframe only, no crash on missing audio.

        src_rec = self.app.record('ReadinessSource', 'src-silent')
        self.app.put('ReadinessMediaAsset', 'silent-clip',
                     {'source': 'src-silent', 'revision': src_rec['revision'],
                      'duration_ms': 600, 'extraction': ex['id']}, 'available')
        self.app.generate_source('s-txt', '.txt', 'Silent clip companion text')
        grant(self.app, 's-txt', ('retrieve', 'adapt'))
        tx = parse(self.app, 's-txt', 'p-txt')
        tp = tx['parts'][0]
        self.app.draft_unit_from_span(
            'u-silent', span_ref(tx['id'], tp, tp['segments'][0]),
            title='Silent drill', statement='Companion text',
            facets={'domain': ['football'], 'learner': ['coach'], 'training_population': ['youth']},
        )
        self.app.review_unit('u-silent', 'op', 'approved', 'ok')

        run = produce(self.app, {
            'track': 'football', 'query': 'Silent',
            'timeline': [{'asset': 'silent-clip', 'start_ms': 0, 'end_ms': 400}],
        }, 'silent-run')
        self.assertEqual(run['state'], 'draft_ready', run.get('error'))
        self.assertTrue(artifact(self.app, run['id'], 'clip.mp4').is_file())

    def test_workbench_impact_span_correct_unit_draft_and_persistent_mode(self):
        ws_dir = Path(self.tmp.name) / 'persistent-ws'
        wb = Workbench(Path(self.tmp.name) / 'unused', persistent_workspace=ws_dir, authorized_local_intake=True)
        with wb.open_app() as app:
            app.generate_source('s-wb', '.txt', 'Synthetic workbench source')
            grant(app, 's-wb', ('retrieve', 'adapt'))
            ex = parse(app, 's-wb', 'p-wb')
            part = ex['parts'][0]
            ref = span_ref(ex['id'], part, part['segments'][0])
        with serve(wb, 0) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def send(route, body=None, headers=None):
                with closing(http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)) as client:
                    client.request('GET' if body is None else 'POST', route,
                                   None if body is None else json.dumps(body), headers or {})
                    resp = client.getresponse()
                    return resp.status, json.loads(resp.read().decode('utf8'))
            try:
                status, state = send('/api/state')
                self.assertEqual(status, 200)
                self.assertTrue(state['persistent'])
                headers = {'Content-Type': 'application/json', 'Origin': base, 'X-Workbench-Session': state['session']}

                # Reset is blocked on persistent workspaces.
                self.assertEqual(send('/api/reset', {}, headers)[0], 403)

                # Span correction via HTTP API.
                st, corr = send('/api/span-correct', {'id': 'c1', 'ref': ref, 'content': 'Corrected span text', 'note': 'fix typo'}, headers)
                self.assertEqual(st, 200)
                self.assertEqual(corr['corrected_ref']['layer'], 'human')

                # Draft candidate unit from corrected span.
                st, drafted = send('/api/unit-draft', {
                    'id': 'u-draft', 'ref': corr['corrected_ref'], 'title': 'Drafted unit',
                    'facets': {'domain': ['football'], 'learner': ['coach'], 'training_population': ['youth']},
                }, headers)
                self.assertEqual(st, 200)
                self.assertEqual(drafted['state'], 'review_pending')

                # Impact analysis endpoint reflects drafted unit.
                st, impact = send('/api/impact?source=s-wb')
                self.assertEqual(st, 200)
                self.assertEqual([u['id'] for u in impact['direct_units']], ['u-draft'])
            finally:
                server.shutdown()
                thread.join()

    def test_operator_cli_end_to_end(self):
        ws = Path(self.tmp.name) / 'cli-ws'
        self.assertEqual(readiness_cli.main(['init', '--workspace', str(ws)]), 0)
        staged = ws / 'data/readiness/raw/staging/sample.txt'
        staged.write_text('Synthetic CLI governance witness', encoding='utf8')
        self.assertEqual(readiness_cli.main([
            'intake', '--workspace', str(ws), '--id', 'cli-src', '--staged', 'sample.txt',
            '--title', 'CLI sample', '--provider', 'Generated', '--owner', 'op', '--sensitivity', 'internal',
        ]), 0)
        self.assertEqual(readiness_cli.main([
            'grant', '--workspace', str(ws), '--id', 'cli-src', '--reviewer', 'op',
            '--proof', 'proof-1', '--scopes', 'internal_parse,retrieve,adapt', '--expires-at', EXPIRY,
        ]), 0)
        self.assertEqual(readiness_cli.main([
            'parse', '--workspace', str(ws), '--id', 'cli-src', '--key', 'cli-parse', '--stages', 'native,chunk',
        ]), 0)
        self.assertEqual(readiness_cli.main(['impact', '--workspace', str(ws), '--source', 'cli-src', '--authorized-local-intake']), 0)
        with Readiness(ws, authorized_local_intake=True) as app:
            backup = app.backup()
        restored = Path(self.tmp.name) / 'cli-restored.sqlite3'
        self.assertEqual(readiness_cli.main(['restore', '--backup', backup['path'], '--target', str(restored)]), 0)
        self.assertTrue(restored.is_file())

    def test_eval_harness_passes(self):
        report = run_eval(unit_count=40)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(report['safety']['domain_refusal_rate'], 1.0)
        self.assertTrue(report['safety']['prompt_injection_contained'])
        self.assertEqual(report['scale']['lexical_disk_hash_checks'], 1)
        self.assertEqual(report['scale']['hybrid_second_cache_hits'], 40)


if __name__ == '__main__':
    unittest.main()

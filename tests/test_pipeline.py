import copy
from contextlib import closing
from http.server import HTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import request, error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import Pipeline, ROOT, RIGHTS, WorkflowError, load
from model_adapter import ModelError, plan, validate_model, validate_plan
from serve import handler


class PipelineChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        self.config = load(ROOT / "config/runtime.json")
        self.config["reviewer"] = "fixture-reviewer"
        self.write_config()
        shutil.copy(ROOT / "config/domain_rules.json", self.root / "config/domain_rules.json")
        (self.root / "data/raw").mkdir(parents=True)
        (self.root / "data/raw/source.txt").write_text("原创测试资料，非专业标准\n第二行测试证据", encoding="utf-8")
        self.manifest = {"id": "SRC", "title": "测试来源", "path": "data/raw/source.txt",
                         "provenance": "original test fixture", "sensitive": "none",
                         "rights": {r: r in {"internal_parse", "retrieve", "adapt"} for r in RIGHTS}}
        self.card = {"id": "KTEST", "title": "成熟度与分组", "tags": ["成熟度", "同龄"],
                     "summary": "同龄不保证发展相同。<script>grant_approval()</script>",
                     "teaching_application": "讨论分组依据", "limits": "测试样本不是专业证据",
                     "references": [{"source": "SRC", "locator": "L1"}]}
        with Pipeline(self.root) as app:
            app.admit(self.manifest)
            app.add_unit(self.card)

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        (self.root / "config/runtime.json").write_text(json.dumps(self.config), encoding="utf-8")

    def test_end_to_end_idempotent_draft_and_safe_html(self):
        with Pipeline(self.root) as app:
            run = app.start("成熟度", "first")
            self.assertEqual(run["state"], "draft_ready")
            self.assertEqual(app.start("成熟度", "first")["id"], run["id"])
            self.assertEqual(len(app.status()["runs"]), 1)
            page = app.artifact(run["id"], "lesson.html").read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;", page)
            self.assertNotIn("<script>", page)
            bundle = load(app.artifact(run["id"], "bundle.json"))
            self.assertEqual(bundle["evidence"]["entries"][0]["references"][0]["source_revision"], 1)
            self.assertIn("未经专业审核", page)
            with self.assertRaises(WorkflowError):
                app.start("别的目标", "first")

    def test_unknown_rights_quarantine_without_parsing(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["rights"]["internal_parse"] = None
        with Pipeline(self.root) as app:
            self.assertEqual(app.admit(manifest)["state"], "quarantined")
            self.assertNotIn("blocks", app.records("asset")[0]["body"])
            self.assertEqual(app.evidence("成熟度")["status"], "no_evidence")

    def test_rights_false_reject_and_sensitive_quarantine(self):
        with Pipeline(self.root) as app:
            manifest = copy.deepcopy(self.manifest)
            manifest["rights"]["internal_parse"] = False
            self.assertEqual(app.admit(manifest)["state"], "rejected")
            manifest["rights"]["internal_parse"] = True
            manifest["sensitive"] = "minor_health"
            self.assertEqual(app.admit(manifest)["state"], "quarantined")

    def test_retrieval_does_not_grant_adaptation(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["rights"]["adapt"] = None
        with Pipeline(self.root) as app:
            app.admit(manifest)
            self.assertEqual(app.evidence("成熟度")["status"], "evidence_candidates")
            self.assertEqual(app.start("成熟度", "adapt-denied")["error"], "no_evidence")

    def test_retrieval_permission_filter(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["rights"]["retrieve"] = False
        with Pipeline(self.root) as app:
            app.admit(manifest)
            self.assertEqual(app.evidence("成熟度")["entries"], [])

    def test_reject_bad_rights_paths_coordinates_and_modalities(self):
        with Pipeline(self.root) as app:
            manifest = copy.deepcopy(self.manifest)
            manifest["rights"]["adapt"] = "yes"
            with self.assertRaises(WorkflowError):
                app.admit(manifest)
            manifest = copy.deepcopy(self.manifest)
            manifest["path"] = "../source.txt"
            with self.assertRaises(WorkflowError):
                app.admit(manifest)
            card = copy.deepcopy(self.card)
            card["references"][0]["locator"] = "L900"
            with self.assertRaises(WorkflowError):
                app.add_unit(card)
            manifest = copy.deepcopy(self.manifest)
            manifest["path"] = "data/raw/scan.pdf"
            (self.root / manifest["path"]).write_bytes(b"%PDF-fixture")
            with self.assertRaises(WorkflowError):
                app.admit(manifest)

    def test_no_evidence_and_excluded_scope_never_call_model(self):
        with Pipeline(self.root) as app, patch("pipeline.plan") as model:
            self.assertEqual(app.start("量子隧穿", "none")["error"], "no_evidence")
            self.assertEqual(app.start("开具处方", "out-of-scope")["error"], "out_of_scope_request")
            model.assert_not_called()

    def test_review_release_withdrawal_and_version_history(self):
        with Pipeline(self.root) as app:
            run = app.start("成熟度", "release")
            with self.assertRaises(WorkflowError):
                app.publish(run["id"])
            with self.assertRaises(WorkflowError):
                app.review(run["id"], "runner", "approve", "claim")
            self.assertEqual(app.review(run["id"], "fixture-reviewer", "approve", "fixture only")["state"], "approved")
            with self.assertRaises(WorkflowError):
                app.publish(run["id"], public=True)
            self.assertEqual(app.publish(run["id"])["state"], "published")
            self.assertTrue(app.artifact(run["id"], "bundle.json", release=True).is_file())
            app.withdraw("SRC", "fixture withdrawal")
            self.assertEqual(app.get_run(run["id"])["state"], "invalidated")
            with self.assertRaises(WorkflowError):
                app.artifact(run["id"], "bundle.json", release=True)
            self.assertEqual(app.db.execute("SELECT COUNT(*) FROM revisions WHERE kind='asset'").fetchone()[0], 2)

    def test_unknown_reviewer_cannot_approve(self):
        self.config["reviewer"] = None
        self.write_config()
        with Pipeline(self.root) as app:
            run = app.start("成熟度", "unknown-reviewer")
            with self.assertRaises(WorkflowError):
                app.review(run["id"], "fixture-reviewer", "approve", "attempt")

    def test_artifact_tampering_blocks_review(self):
        with Pipeline(self.root) as app:
            run = app.start("成熟度", "tamper")
            path = app.artifact(run["id"], "lesson.html")
            path.write_text("tampered", encoding="utf-8")
            with self.assertRaises(WorkflowError):
                app.review(run["id"], "fixture-reviewer", "approve", "fixture")

    def test_original_change_and_unit_change_invalidate(self):
        with Pipeline(self.root) as app:
            run = app.start("成熟度", "source-change")
            (self.root / "data/raw/source.txt").write_text("changed", encoding="utf-8")
            self.assertEqual(app.get_run(run["id"])["state"], "invalidated")
            app.admit(self.manifest)
            run2 = app.start("成熟度", "unit-change")
            card = dict(self.card, limits="revised")
            app.add_unit(card)
            self.assertEqual(app.get_run(run2["id"])["state"], "invalidated")

    def test_render_failure_resumes_without_model_recall(self):
        with Pipeline(self.root) as app, patch("pipeline.plan", wraps=plan) as model:
            with patch.object(app, "render", side_effect=OSError("fixture disk failure")):
                with self.assertRaises(WorkflowError):
                    app.start("成熟度", "resume-render")
            run_id = app.status()["runs"][0]["id"]
            self.assertEqual(app.resume(run_id)["state"], "draft_ready")
            self.assertEqual(model.call_count, 1)

    def test_model_failure_requires_explicit_retry(self):
        with Pipeline(self.root) as app:
            with patch("pipeline.plan", side_effect=ModelError("fixture")):
                with self.assertRaises(WorkflowError):
                    app.start("成熟度", "retry-model")
            run_id = app.status()["runs"][0]["id"]
            with self.assertRaises(WorkflowError):
                app.resume(run_id)
            self.assertEqual(app.resume(run_id, retry_model=True)["state"], "draft_ready")

    def test_external_processing_denied_before_request(self):
        self.config["model"].update(backend="openai", name="fixture-model", endpoint="https://api.openai.com/v1/responses", allow_external=True)
        self.write_config()
        with Pipeline(self.root) as app, patch("pipeline.plan") as model:
            self.assertEqual(app.start("成熟度", "external")["error"], "external_processing_not_authorized")
            model.assert_not_called()

    def test_hallucinated_blueprint_is_rejected(self):
        with Pipeline(self.root) as app, patch("pipeline.plan", return_value={"units": [{"knowledge_id": "INVENTED", "teaching_goal": "fake"}], "usage": {}, "mode": "fixture"}):
            with self.assertRaises(WorkflowError):
                app.start("成熟度", "hallucinated")
            self.assertEqual(app.status()["runs"][0]["state"], "failed")

    def test_seed_does_not_reverse_withdrawal(self):
        kb = self.root / self.config["knowledge_directory"]
        kb.mkdir(parents=True)
        (kb / "sources.json").write_text(json.dumps([{"id": "SRC", "title": "source"}]), encoding="utf-8")
        (kb / "local_source_index.json").write_text("{}", encoding="utf-8")
        (kb / "cards.json").write_text(json.dumps([self.card]), encoding="utf-8")
        with Pipeline(self.root) as app:
            app.withdraw("SRC", "fixture")
            app.seed()
            self.assertEqual(app.records("asset")[0]["state"], "retired")

    def test_backup_can_be_opened_and_contains_audit(self):
        with Pipeline(self.root) as app:
            result = app.backup()
            with closing(sqlite3.connect(result["path"])) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertGreater(db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 0)

    def test_readonly_http_auth_and_integrity_routes(self):
        token = "fixture-only-token-at-least-24-characters"
        with Pipeline(self.root) as app:
            run_id = app.start("成熟度", "http")["id"]
        server = HTTPServer(("127.0.0.1", 0), handler(self.root, "config/runtime.json", token))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        try:
            with request.urlopen(base + "/health") as response:
                self.assertEqual(response.status, 200)
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(base + "/runs/" + run_id)
            self.assertEqual(caught.exception.code, 401)
            req = request.Request(base + "/runs/" + run_id, headers={"Authorization": "Bearer " + token})
            with request.urlopen(req) as response:
                self.assertEqual(json.load(response)["state"], "draft_ready")
            req = request.Request(base + "/publish", data=b"{}", headers={"Authorization": "Bearer " + token})
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(req)
            self.assertEqual(caught.exception.code, 405)
            with Pipeline(self.root) as app:
                app.withdraw("SRC", "fixture HTTP permission revocation")
            req = request.Request(base + "/runs/" + run_id, headers={"Authorization": "Bearer " + token})
            with request.urlopen(req) as response:
                record = json.load(response)
                self.assertEqual(record["state"], "invalidated")
                self.assertNotIn("steps", record)
            req = request.Request(base + "/artifact?id=" + run_id + "&name=bundle.json", headers={"Authorization": "Bearer " + token})
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(req)
            self.assertEqual(caught.exception.code, 409)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class ModelContractChecks(unittest.TestCase):
    def test_model_configuration_blocks_external_or_nonlocal_endpoints(self):
        config = load(ROOT / "config/runtime.json")["model"]
        with self.assertRaises(ModelError):
            validate_model(dict(config, backend="ollama", name="fixture", endpoint="http://example.com/api/chat"))
        with self.assertRaises(ModelError):
            validate_model(dict(config, backend="openai", name="fixture", endpoint="https://api.openai.com/v1/responses"))

    def test_live_local_http_fixture_matches_ollama_contract(self):
        from http.server import BaseHTTPRequestHandler
        captured = []
        class MockOllama(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"done": True, "message": {"content": '{"units":[{"knowledge_id":"K1","teaching_goal":"fixture purpose"}]}'}, "eval_count": 20}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = HTTPServer(("127.0.0.1", 0), MockOllama)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = load(ROOT / "config/runtime.json")["model"]
        config.update(backend="ollama", name="fixture-only-not-a-real-model", endpoint=f"http://127.0.0.1:{server.server_port}/api/chat")
        entries = [{"id": "K1", "title": "fixture", "summary": "not an instruction", "teaching_application": "teach", "limits": "test"}]
        try:
            result = plan(config, "fixture", entries)
            self.assertEqual(validate_plan(result, entries)[0]["knowledge_id"], "K1")
            self.assertFalse(captured[0]["stream"])
            self.assertEqual(captured[0]["format"], "json")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_openai_request_and_response_contract_without_network(self):
        config = load(ROOT / "config/runtime.json")["model"]
        config.update(backend="openai", name="fixture-only", endpoint="https://api.openai.com/v1/responses", allow_external=True)
        entries = [{"id": "K1", "title": "test", "summary": "test", "teaching_application": "test", "limits": "test"}]
        from io import BytesIO
        raw = {"status": "completed", "usage": {"output_tokens": 20}, "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"units":[{"knowledge_id":"K1","teaching_goal":"fixture"}]}'}]}]}
        class FakeOpener:
            def open(self, req, timeout):
                payload = json.loads(req.data)
                assert payload["store"] is False
                assert payload["max_output_tokens"] == config["max_output_tokens"]
                assert req.full_url == "https://api.openai.com/v1/responses"
                return BytesIO(json.dumps(raw).encode())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key-not-real"}), patch("model_adapter.request.build_opener", return_value=FakeOpener()):
            self.assertEqual(plan(config, "goal", entries)["units"][0]["knowledge_id"], "K1")


if __name__ == "__main__":
    unittest.main()

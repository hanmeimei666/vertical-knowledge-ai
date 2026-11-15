import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from import_resources import prepare, commit, write
from pipeline import Pipeline, ROOT, RIGHTS, WorkflowError, load
from smoke_import import smoke


class ImportChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        shutil.copy(ROOT / "config/runtime.json", self.root / "config/runtime.json")
        shutil.copy(ROOT / "config/domain_rules.json", self.root / "config/domain_rules.json")
        (self.root / "data/raw").mkdir(parents=True)
        (self.root / "data/raw/resource.txt").write_text("来源记录与版本必须能回查。\n来源记录应保留适用限制。", encoding="utf-8")
        self.manifest = {"id": "NEW", "title": "原创流程样本", "path": "data/raw/resource.txt",
                         "provenance": "Synthetic original", "sensitive": "none",
                         "rights": {r: r in {"internal_parse", "retrieve", "adapt"} for r in RIGHTS}}

    def tearDown(self):
        self.temp.cleanup()

    def curate(self, app):
        prepare(app, self.manifest, "new-batch")
        path = self.root / "data/imports/new-batch/candidates.json"
        document = load(path)
        document["units"][0].update(include=True, confirmed=True)
        document["units"][0]["knowledge"].update(title="来源记录", tags=["来源记录"], teaching_application="练习来源回查")
        write(path, document)
        return path, document

    def test_new_resource_runs_and_is_idempotent(self):
        with Pipeline(self.root) as app:
            self.curate(app)
            first = commit(app, "new-batch", "来源记录", "new-run")
            second = commit(app, "new-batch", "来源记录", "new-run")
            self.assertEqual(first["run"]["state"], "draft_ready")
            self.assertEqual(first["run"]["id"], second["run"]["id"])
            self.assertEqual(len(app.records("unit")), 1)
            page = app.artifact(first["run"]["id"], "lesson.md").read_text(encoding="utf-8")
            self.assertIn("原文摘录", page)

    def test_unconfirmed_or_placeholder_candidates_are_not_imported(self):
        with Pipeline(self.root) as app:
            path, doc = self.curate(app)
            doc["units"][0]["confirmed"] = False
            write(path, doc)
            with self.assertRaises(WorkflowError):
                commit(app, "new-batch", "来源记录", "unconfirmed")
            doc["units"][0]["confirmed"] = True
            doc["units"][0]["knowledge"]["teaching_application"] = "请填写教学用法"
            write(path, doc)
            with self.assertRaises(WorkflowError):
                commit(app, "new-batch", "来源记录", "unfinished")
            self.assertEqual(app.records("unit"), [])

    def test_whole_selection_validated_before_writing_any_unit(self):
        with Pipeline(self.root) as app:
            path, doc = self.curate(app)
            doc["units"][1].update(include=True, confirmed=True)
            doc["units"][1]["knowledge"]["teaching_application"] = "练习来源回查"
            doc["units"][1]["knowledge"]["references"][0]["locator"] = "L900"
            write(path, doc)
            with self.assertRaises(WorkflowError):
                commit(app, "new-batch", "来源记录", "invalid-coordinate")
            self.assertEqual(app.records("unit"), [])

    def test_excerpt_cannot_be_silently_rewritten_as_original(self):
        with Pipeline(self.root) as app:
            path, doc = self.curate(app)
            doc["units"][0]["knowledge"]["summary"] = "与原文不同的编辑归纳"
            write(path, doc)
            with self.assertRaises(WorkflowError):
                commit(app, "new-batch", "来源记录", "bad-quote")
            doc["units"][0]["knowledge"]["origin"] = "curator_summary"
            write(path, doc)
            self.assertEqual(commit(app, "new-batch", "来源记录", "curated")["run"]["state"], "draft_ready")

    def test_unknown_rights_create_no_text_candidates(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["rights"]["internal_parse"] = None
        with Pipeline(self.root) as app:
            result = prepare(app, manifest, "unknown")
            self.assertEqual(result["state"], "quarantined")
            self.assertEqual(result["candidates"], 0)

    def test_source_change_or_withdrawal_blocks_stale_candidates(self):
        with Pipeline(self.root) as app:
            self.curate(app)
            app.withdraw("NEW", "fixture withdrawal")
            with self.assertRaises(WorkflowError):
                commit(app, "new-batch", "来源记录", "stale")
            app.admit(self.manifest)
            with self.assertRaises(WorkflowError):
                commit(app, "new-batch", "来源记录", "re-admitted")
            (self.root / "data/raw/resource.txt").write_text("changed", encoding="utf-8")
            with self.assertRaises(WorkflowError):
                commit(app, "new-batch", "来源记录", "changed")

    def test_existing_batch_is_never_overwritten_and_limit_is_reported(self):
        with Pipeline(self.root) as app:
            report = prepare(app, self.manifest, "limited", limit=1)
            self.assertEqual(report["omitted_blocks"], 1)
            with self.assertRaises(WorkflowError):
                prepare(app, self.manifest, "limited")

    def test_old_knowledge_cannot_substitute_for_unmatched_new_resource(self):
        with Pipeline(self.root) as app:
            path, doc = self.curate(app)
            old = dict(doc["units"][0]["knowledge"], id="OLD", title="量子隧穿", tags=["量子隧穿"])
            app.add_unit(old)
            self.assertTrue(app.evidence("量子隧穿")["entries"])
            result = commit(app, "new-batch", "量子隧穿", "scoped")
            self.assertEqual(result["run"]["error"], "no_evidence")

    def test_changed_curation_requires_new_run_key(self):
        with Pipeline(self.root) as app:
            path, doc = self.curate(app)
            commit(app, "new-batch", "来源记录", "same-key")
            doc["units"][0]["knowledge"]["limits"] = "修订边界"
            write(path, doc)
            with self.assertRaises(WorkflowError):
                commit(app, "new-batch", "来源记录", "same-key")

    def test_clean_demo_needs_no_private_originals(self):
        (self.root / "examples/resources").mkdir(parents=True)
        shutil.copy(ROOT / "examples/resources/import-demo.txt", self.root / "examples/resources/import-demo.txt")
        report = smoke(self.root)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["model_calls"], 0)
        self.assertTrue(all(Path(p).is_file() for p in report["artifacts"].values()))


if __name__ == "__main__":
    unittest.main()

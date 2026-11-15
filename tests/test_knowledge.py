import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import knowledge


class KnowledgeChecks(unittest.TestCase):
    def test_real_course_and_source_coordinates(self):
        self.assertEqual(knowledge.verify()["status"], "PASS")

    def test_maturation_retrieves_relevant_cards(self):
        results = knowledge.search("成熟度", 3)["results"]
        self.assertEqual(results[0]["id"], "K03")
        self.assertTrue(all(r["references"] for r in results))

    def test_prediction_boundary_is_retrievable(self):
        self.assertEqual(knowledge.search("损伤预测", 1)["results"][0]["id"], "K10")

    def test_unrelated_query_does_not_invent_answer(self):
        self.assertEqual(knowledge.search("量子隧穿")["status"], "no_evidence")

    def test_empty_query_returns_no_evidence(self):
        self.assertEqual(knowledge.search(" ")["results"], [])

    def test_broken_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            for path in knowledge.KB.glob("*.json"):
                (target / path.name).write_bytes(path.read_bytes())
            cards = knowledge.read(target / "cards.json")
            cards[0]["references"][0]["source"] = "MISSING"
            (target / "cards.json").write_text(json.dumps(cards), encoding="utf-8")
            with patch.object(knowledge, "KB", target):
                self.assertEqual(knowledge.verify()["status"], "FAIL")

    def test_wrong_hours_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            course = knowledge.read(knowledge.COURSE / "curriculum.json")
            course["lessons"][0]["hours"] = 4
            (target / "curriculum.json").write_text(json.dumps(course), encoding="utf-8")
            with patch.object(knowledge, "COURSE", target):
                self.assertEqual(knowledge.verify()["status"], "FAIL")

    def test_invalid_local_coordinate_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            for path in knowledge.KB.glob("*.json"):
                (target / path.name).write_bytes(path.read_bytes())
            cards = knowledge.read(target / "cards.json")
            cards[0]["references"][1]["locator"] = "B99999"
            (target / "cards.json").write_text(json.dumps(cards), encoding="utf-8")
            with patch.object(knowledge, "KB", target):
                self.assertEqual(knowledge.verify()["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()

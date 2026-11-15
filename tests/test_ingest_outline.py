from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_outline


class IngestChecks(unittest.TestCase):
    def test_missing_samples_does_not_overwrite_existing_index(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            index = root / "knowledge/youth-conditioning/local_source_index.json"
            index.parent.mkdir(parents=True)
            index.write_text('{"keep": true}', encoding="utf-8")
            with patch.object(ingest_outline, "ROOT", root), patch.object(ingest_outline, "RAW", root / "missing"):
                with self.assertRaises(SystemExit):
                    ingest_outline.main()
            self.assertEqual(index.read_text(encoding="utf-8"), '{"keep": true}')


if __name__ == "__main__":
    unittest.main()

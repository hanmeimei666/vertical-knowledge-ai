"""Extract native DOCX blocks with stable body/table coordinates, without page claims."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/youth-conditioning"
OUT = ROOT / "data/derived/youth-conditioning"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def text(node):
    return "".join(t.text or "" for t in node.iter(W + "t")).strip()


def main():
    paths = sorted(RAW.glob("*.docx"))
    if not paths:
        raise SystemExit(f"No local DOCX samples in {RAW}; existing source index was not changed.")
    OUT.mkdir(parents=True, exist_ok=True)
    assets = []
    for path in paths:
        asset_id = "LOCAL-PLAN" if path.name.startswith("02.1_") else "LOCAL-STANDARD"
        with zipfile.ZipFile(path) as archive:
            body = ET.fromstring(archive.read("word/document.xml")).find(W + "body")
        blocks = []
        for index, node in enumerate(body, 1):
            if node.tag == W + "p" and text(node):
                blocks.append({"locator": f"B{index}", "text": text(node)})
            elif node.tag == W + "tbl":
                for row_index, row in enumerate(node.findall(W + "tr"), 1):
                    cells = [text(cell) for cell in row.findall(W + "tc")]
                    blocks.append({"locator": f"B{index}.R{row_index}", "text": " | ".join(cells)})
        asset = {"id": asset_id, "filename": path.name,
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "locator_scheme": "word/document.xml body child Bn; table row Rn; not page numbers",
                 "authorization": "user authorized project parsing, quotation and adaptation on 2026-11-14",
                 "third_party_redistribution": "not_inferred", "blocks": blocks}
        (OUT / f"{asset_id}.json").write_text(json.dumps(asset, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / f"{asset_id}.md").write_text("\n\n".join(f"[{b['locator']}] {b['text']}" for b in blocks), encoding="utf-8")
        assets.append({k: v for k, v in asset.items() if k != "blocks"})
    (OUT / "manifest.json").write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    index = {}
    for asset in assets:
        parsed = json.loads((OUT / f"{asset['id']}.json").read_text(encoding="utf-8"))
        index[asset["id"]] = {"filename": asset["filename"],
                              "relative_path": f"data/raw/youth-conditioning/{asset['filename']}",
                              "sha256": asset["sha256"],
                              "locators": [b["locator"] for b in parsed["blocks"]]}
    index_path = ROOT / "knowledge/youth-conditioning/local_source_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"assets": len(assets), "output": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Local knowledge workflow: admission, evidence, planning, draft, review and release."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import uuid
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import urlsplit

from model_adapter import plan, validate_model, validate_plan

ROOT = Path(__file__).resolve().parents[1]
RIGHTS = ("internal_parse", "retrieve", "adapt", "redistribute", "model_training", "external_model_processing")


class WorkflowError(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def inside(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise WorkflowError("Path must stay inside the project")
    return target


def now():
    return datetime.now(timezone.utc).isoformat()


def tokens(text):
    text = text.lower()
    result = set(re.findall(r"[a-z0-9]+", text))
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(part[i:i+2] for i in range(len(part)-1))
        if len(part) == 1:
            result.add(part)
    return result


def parse_native(path):
    if path.suffix.lower() in {".txt", ".md"}:
        return [{"locator": f"L{i}", "text": line} for i, line in
                enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1) if line.strip()]
    if path.suffix.lower() != ".docx":
        raise WorkflowError("Unsupported modality; use native DOCX/TXT/MD, other parsers are not enabled")
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        member = archive.getinfo("word/document.xml")
        if member.file_size > 20_000_000:
            raise WorkflowError("DOCX XML exceeds the native parser limit")
        body = ET.fromstring(archive.read(member)).find(w + "body")
    blocks = []
    def text(node):
        return "".join(t.text or "" for t in node.iter(w + "t")).strip()
    for i, node in enumerate(body, 1):
        if node.tag == w + "p" and text(node):
            blocks.append({"locator": f"B{i}", "text": text(node)})
        elif node.tag == w + "tbl":
            for j, row in enumerate(node.findall(w + "tr"), 1):
                blocks.append({"locator": f"B{i}.R{j}", "text": " | ".join(text(c) for c in row.findall(w + "tc"))})
    return blocks


class Pipeline:
    def __init__(self, root=ROOT, config="config/runtime.json"):
        self.root = Path(root).resolve()
        self.config_path = inside(self.root, config)
        self.config = load(self.config_path)
        c = self.config
        if c["schema_version"] != 1 or not c["domain"]:
            raise WorkflowError("Unsupported runtime schema or missing domain")
        for key in ("knowledge_directory", "raw_directory", "database", "output_directory", "rules_file"):
            inside(self.root, c[key])
        if not 1 <= c["retrieval"]["limit"] <= 10 or c["retrieval"]["minimum_score"] < 1:
            raise WorkflowError("Invalid retrieval limits")
        if c["service"]["host"] != "127.0.0.1" or not 1024 <= c["service"]["port"] <= 65535:
            raise WorkflowError("Local service must use 127.0.0.1 and port 1024..65535")
        validate_model(c["model"])
        self.rules = load(inside(self.root, c["rules_file"]))
        if self.rules["review_required"] is not True or self.rules["output_kind"] != "teaching_draft":
            raise WorkflowError("This workflow requires reviewed teaching drafts")
        db_path = inside(self.root, c["database"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS records (
            kind TEXT, id TEXT, revision INTEGER, sha TEXT, state TEXT, body TEXT,
            PRIMARY KEY(kind,id));
          CREATE TABLE IF NOT EXISTS revisions (
            kind TEXT, id TEXT, revision INTEGER, sha TEXT, state TEXT, body TEXT, created TEXT,
            PRIMARY KEY(kind,id,revision));
          CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, request_key TEXT UNIQUE, request_hash TEXT, request TEXT,
            state TEXT, input_hash TEXT, snapshot TEXT, steps TEXT, error TEXT, review TEXT,
            created TEXT, updated TEXT);
          CREATE TABLE IF NOT EXISTS audit (
            seq INTEGER PRIMARY KEY, created TEXT, action TEXT, object_id TEXT, metadata TEXT);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def audit(self, action, object_id, metadata=None):
        self.db.execute("INSERT INTO audit(created,action,object_id,metadata) VALUES(?,?,?,?)",
                        (now(), action, object_id, encode(metadata or {})))

    def records(self, kind):
        return [dict(r, body=json.loads(r["body"])) for r in
                self.db.execute("SELECT * FROM records WHERE kind=? ORDER BY id", (kind,))]

    def put(self, kind, id_, body, state):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", id_):
            raise WorkflowError("Invalid record ID")
        sha = digest({"body": body, "state": state})
        previous = self.db.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, id_)).fetchone()
        if previous and previous["sha"] == sha:
            return previous["revision"]
        revision = previous["revision"] + 1 if previous else 1
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?)",
                            (kind, id_, revision, sha, state, encode(body)))
            self.db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)",
                            (kind, id_, revision, sha, state, encode(body), now()))
            self.db.execute("UPDATE runs SET state='invalidated',updated=? WHERE state NOT IN ('invalidated','blocked')", (now(),))
            self.audit("record_revision", id_, {"kind": kind, "revision": revision, "state": state})
        return revision

    def admit(self, manifest):
        if not isinstance(manifest.get("title"), str) or not manifest["title"].strip():
            raise WorkflowError("Source title required")
        rights = manifest.get("rights", {})
        if set(rights) != set(RIGHTS) or any(v is not True and v is not False and v is not None for v in rights.values()):
            raise WorkflowError("All six rights must be true, false or null")
        if manifest.get("sensitive") not in {"none", "unknown", "personal", "minor_health"}:
            raise WorkflowError("Explicit sensitive-data classification required")
        if not manifest.get("provenance"):
            raise WorkflowError("Source provenance required")
        body = dict(manifest)
        state = "admitted"
        if rights["internal_parse"] is False:
            state = "rejected"
        elif rights["internal_parse"] is not True or manifest["sensitive"] != "none":
            state = "quarantined"
        if manifest.get("path"):
            path = inside(self.root, manifest["path"])
            if not path.is_relative_to(inside(self.root, self.config["raw_directory"])):
                raise WorkflowError("Originals must be inside the configured raw directory")
            if not path.is_file():
                state = "quarantined"
                body["admission_note"] = "Original file missing; no parsing or retrieval"
            else:
                if path.stat().st_size > 50_000_000:
                    raise WorkflowError("Native sample limit is 50 MB")
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if manifest.get("sha256") and manifest["sha256"] != sha:
                    raise WorkflowError("Original fingerprint mismatch")
                body["sha256"] = sha
                if state == "admitted":
                    try:
                        body["blocks"] = parse_native(path)
                    except (zipfile.BadZipFile, ET.ParseError, UnicodeError, KeyError, TypeError) as exc:
                        raise WorkflowError("Native parsing failed (" + type(exc).__name__ + ")") from None
                    if not body["blocks"]:
                        raise WorkflowError("No native text; OCR is not enabled")
                    body["locators"] = [b["locator"] for b in body["blocks"]]
        elif manifest.get("representation") != "curated_summary":
            raise WorkflowError("A raw path or curated_summary representation is required")
        revision = self.put("asset", manifest["id"], body, state)
        return {"id": manifest["id"], "state": state, "revision": revision}

    def add_unit(self, card):
        for field in ("id", "title", "summary", "teaching_application", "limits", "tags", "references"):
            if not card.get(field):
                raise WorkflowError("Missing knowledge field: " + field)
        if not all(isinstance(card[f], str) for f in ("id", "title", "summary", "teaching_application", "limits")):
            raise WorkflowError("Knowledge text fields must be strings")
        if not isinstance(card["tags"], list) or not all(isinstance(t, str) for t in card["tags"]):
            raise WorkflowError("Knowledge tags must be strings")
        if not isinstance(card["references"], list) or not all(isinstance(r, dict) and
                isinstance(r.get("source"), str) and isinstance(r.get("locator"), str) for r in card["references"]):
            raise WorkflowError("Knowledge references must contain source and locator strings")
        assets = {a["id"]: a for a in self.records("asset")}
        for ref in card["references"]:
            asset = assets.get(ref["source"])
            if not asset or not isinstance(ref["locator"], str) or not ref["locator"].strip():
                raise WorkflowError("Missing evidence source or locator")
            if asset["body"].get("locators") is not None and ref["locator"] not in asset["body"]["locators"]:
                raise WorkflowError("Unknown source coordinate")
        body = dict(card, origin="curator_summary", review_state="review_pending")
        return {"id": card["id"], "revision": self.put("unit", card["id"], body, "review_pending")}

    def seed(self):
        kb = inside(self.root, self.config["knowledge_directory"])
        local = load(kb / "local_source_index.json")
        results = []
        for source in load(kb / "sources.json"):
            manifest = {"id": source["id"], "title": source["title"], "metadata": source,
                        "representation": "curated_summary", "sensitive": "none",
                        "provenance": "Project source registry; local user authorization / original editorial summaries",
                        "rights": {k: k in {"internal_parse", "retrieve", "adapt"} for k in RIGHTS},
                        "rights_note": "Only internal project use of curated summaries; original redistribution, training and cloud processing not inferred"}
            if source["id"] in local:
                original = local[source["id"]]
                manifest.update(path=original["relative_path"], sha256=original["sha256"], locators=original["locators"])
            # Do not silently override an operator withdrawal or revised rights on re-seed.
            if not self.db.execute("SELECT 1 FROM records WHERE kind='asset' AND id=?", (source["id"],)).fetchone():
                results.append(self.admit(manifest))
        for card in load(kb / "cards.json"):
            self.add_unit(card)
        return {"assets_added": results, "assets_total": len(self.records("asset")), "units": len(self.records("unit"))}

    def fingerprint(self):
        records = [{k: r[k] for k in ("kind", "id", "revision", "sha", "state")} for kind in ("asset", "unit") for r in self.records(kind)]
        for asset in self.records("asset"):
            if asset["state"] == "admitted" and asset["body"].get("path"):
                path = inside(self.root, asset["body"]["path"])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset["body"]["sha256"]:
                    raise WorkflowError("Original source missing/changed; re-admission required: " + asset["id"])
        implementation = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                          for name in ("pipeline.py", "model_adapter.py")}
        return digest({"records": records, "config": self.config, "rules": self.rules, "implementation": implementation})

    def evidence(self, query, purpose="retrieve"):
        if purpose not in {"retrieve", "adapt"}:
            raise WorkflowError("Unsupported evidence purpose")
        self.fingerprint()
        query_tokens = tokens(query.strip())
        assets = {a["id"]: a for a in self.records("asset")}
        results = []
        for unit in self.records("unit"):
            if unit["state"] != "review_pending":
                continue
            card = unit["body"]
            refs = []
            for ref in card["references"]:
                source = assets[ref["source"]]
                body = source["body"]
                if source["state"] != "admitted" or body["rights"]["retrieve"] is not True or body["rights"][purpose] is not True:
                    break
                refs.append(dict(ref, source_revision=source["revision"], source_hash=source["sha"],
                                 original_sha256=body.get("sha256"), rights=body["rights"],
                                 verification=body.get("metadata", {}).get("verification", "native_text_parsed"),
                                 url=body.get("metadata", {}).get("url", ""),
                                 title=body["title"], professional_review="pending"))
            else:
                heading = (card["title"] + " " + " ".join(card["tags"])).lower()
                score = sum(3 * (t in heading) + (t in card["summary"].lower()) for t in query_tokens)
                if query.strip() and query.lower().strip() in heading:
                    score += 6
                if score >= self.config["retrieval"]["minimum_score"]:
                    results.append(dict(card, revision=unit["revision"], hash=unit["sha"], score=score, references=refs))
        results.sort(key=lambda c: (-c["score"], c["id"]))
        return {"schema_version": 1, "status": "evidence_candidates" if results else "no_evidence",
                "method": "Chinese bigram / English keyword matching", "purpose": purpose,
                "entries": results[:self.config["retrieval"]["limit"]],
                "gaps": ["候选匹配不证明完整覆盖，未自动消解跨来源冲突。", "专业审核待完成。"]}

    def save_run(self, run):
        with self.db:
            self.db.execute("UPDATE runs SET state=?,steps=?,error=?,review=?,updated=? WHERE id=?",
                            (run["state"], encode(run["steps"]), run.get("error"), encode(run.get("review")), now(), run["id"]))
            self.audit("run_" + run["state"], run["id"], {"steps": list(run["steps"]), "error": run.get("error")})

    def get_run(self, id_, check=True):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (id_,)).fetchone()
        if not row:
            raise WorkflowError("Unknown run")
        run = dict(row)
        for key in ("request", "snapshot", "steps", "review"):
            run[key] = json.loads(run[key]) if run[key] else None
        if check and run["state"] not in {"invalidated", "blocked"}:
            try:
                stale = run["input_hash"] != self.fingerprint()
            except WorkflowError:
                stale = True
            if stale:
                run["state"] = "invalidated"
                run["error"] = "input_revision_changed"
                self.save_run(run)
        return run

    def start(self, goal, request_key):
        if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 2000:
            raise WorkflowError("Goal must contain 1..2000 characters")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_key):
            raise WorkflowError("Invalid idempotency key")
        spec = {"goal": goal.strip(), "audience": self.rules["audience"], "kind": self.rules["output_kind"]}
        request_hash = digest(spec)
        previous = self.db.execute("SELECT id,request_hash FROM runs WHERE request_key=?", (request_key,)).fetchone()
        if previous:
            if previous["request_hash"] != request_hash:
                raise WorkflowError("Idempotency key already used for another request")
            return self.get_run(previous["id"])
        input_hash = self.fingerprint()
        if not self.records("unit"):
            raise WorkflowError("No knowledge units; run seed or admit/add-unit first")
        run_id = uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                run_id, request_key, request_hash, encode(spec), "created", input_hash,
                encode({"config": self.config, "rules": self.rules, "prompt_version": "evidence-selection-1"}),
                "{}", None, None, now(), now()))
            self.audit("run_created", run_id, {"request_hash": request_hash, "input_hash": input_hash})
        return self.resume(run_id)

    def resume(self, id_, retry_model=False):
        run = self.get_run(id_)
        if run["state"] in {"draft_ready", "approved", "published", "invalidated", "blocked", "rejected"}:
            return run
        if run.get("error") == "model_failed" and not retry_model:
            raise WorkflowError("Model attempt failed; explicit --retry-model required (may incur cost)")
        try:
            if any(p in run["request"]["goal"] for p in self.rules["excluded_request_phrases"]):
                run.update(state="blocked", error="out_of_scope_request")
                self.save_run(run)
                return run
            if "evidence" not in run["steps"]:
                pack = self.evidence(run["request"]["goal"], "adapt")
                run["steps"]["evidence"] = pack
                run.update(state="evidence_ready", error=None)
                if not pack["entries"]:
                    run.update(state="blocked", error="no_evidence")
                self.save_run(run)
                if run["state"] == "blocked":
                    return run
            entries = run["steps"]["evidence"]["entries"]
            if "blueprint" not in run["steps"]:
                external = self.config["model"]["backend"] == "openai"
                if external and any(ref["rights"]["external_model_processing"] is not True for e in entries for ref in e["references"]):
                    run.update(state="blocked", error="external_processing_not_authorized")
                    self.save_run(run)
                    return run
                # Persist attempt before network I/O. An interrupted attempt also requires explicit retry.
                run.update(state="planning", error="model_failed")
                self.save_run(run)
                planned = plan(self.config["model"], run["request"]["goal"], entries)
                units = validate_plan(planned, entries)
                run["steps"]["blueprint"] = {"schema_version": 1, "units": units,
                    "request": run["request"], "review_required": True, "state": "draft",
                    "mode": planned["mode"], "usage": planned["usage"],
                    "model": self.config["model"]["name"], "limitations": self.rules["limitations"]}
                run.update(state="blueprint_ready", error=None)
                self.save_run(run)
            # Check originals/catalog/config again after a potentially long model call.
            if self.get_run(id_)["state"] == "invalidated":
                return self.get_run(id_)
            outputs = self.render(run, "draft")
            run["steps"]["artifacts"] = outputs
            run.update(state="draft_ready", error=None)
            self.save_run(run)
        except Exception as exc:
            failed_while_planning = run["state"] == "planning" or run.get("error") == "model_failed"
            run["state"] = "failed"
            run["error"] = "model_failed" if failed_while_planning else "step_failed"
            self.save_run(run)
            raise WorkflowError("Workflow failed (" + type(exc).__name__ + "); inspect/resume run " + id_) from None
        return run

    def render(self, run, stage):
        folder = inside(self.root, self.config["output_directory"]) / run["id"] / stage
        folder.mkdir(parents=True, exist_ok=True)
        blueprint = run["steps"]["blueprint"]
        pack = run["steps"]["evidence"]
        entries = {e["id"]: e for e in pack["entries"]}
        banner = "教研初稿 · 未经专业审核" if stage == "draft" else "内部审核版本 · 不代表实证有效或允许公开分发"
        def md(text):
            return re.sub(r"([\\`*_[\]<>!#])", r"\\\1", str(text))
        lines = ["# " + md(run["request"]["goal"]), "", banner, "", "对象：" + md(run["request"]["audience"]),
                 "", "运行：" + run["id"], "", "编排：" + blueprint["mode"], ""]
        sections = []
        for unit in blueprint["units"]:
            e = entries[unit["knowledge_id"]]
            lines.extend(["## " + md(e["title"]), "", "编辑归纳：" + md(e["summary"]), "",
                          "教学编排（模型或规则建议）：" + md(unit["teaching_goal"]), "", "限制：" + md(e["limits"]), ""])
            refs_html = []
            for ref in e["references"]:
                citation = f"{ref['source']} r{ref['source_revision']} / {ref['locator']} / {ref['verification']}"
                lines.extend(["- " + md(citation), ""])
                url = ref.get("url", "")
                link = ""
                if urlsplit(url).scheme in {"http", "https"} and not any(c in url for c in '\n\r<>"'):
                    link = ' <a rel="noreferrer" href="' + html.escape(url, quote=True) + '">来源</a>'
                refs_html.append("<li>" + html.escape(citation) + link + "</li>")
            sections.append("<section><h2>" + html.escape(e["title"]) + "</h2><p>编辑归纳：" + html.escape(e["summary"]) +
                "</p><p>教学编排（模型或规则建议）：" + html.escape(unit["teaching_goal"]) + "</p><p>限制：" + html.escape(e["limits"]) +
                "</p><ul>" + "".join(refs_html) + "</ul></section>")
        lines.extend(["## 适用边界与缺口", ""] + ["- " + md(t) for t in self.rules["limitations"] + pack["gaps"]])
        page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'">'
                '<title>知识教研成果</title><style>body{font:18px/1.8 sans-serif;max-width:900px;margin:40px auto;padding:20px;color:#182c31}section{border-top:1px solid #ccc}aside{background:#fff1cc;padding:16px}textarea{width:95%;min-height:90px}</style>'
                '<h1>' + html.escape(run["request"]["goal"]) + '</h1><aside>' + html.escape(banner) + '</aside><p>对象：' + html.escape(run["request"]["audience"]) +
                '</p><p>运行：' + run["id"] + '</p>' + ''.join(sections) +
                '<h2>教研记录</h2><label for="notes">可填写本次讨论的缺口和改进（仅本页临时输入，不会保存）</label><textarea id="notes"></textarea><ul>' +
                ''.join('<li>' + html.escape(t) + '</li>' for t in self.rules["limitations"] + pack["gaps"]) + '</ul></html>')
        payload = {"run_id": run["id"], "input_hash": run["input_hash"], "stage": stage,
                   "blueprint": blueprint, "evidence": pack, "review": run.get("review")}
        contents = {"lesson.md": "\n".join(lines), "lesson.html": page, "bundle.json": json.dumps(payload, ensure_ascii=False, indent=2)}
        outputs = {}
        for filename, content in contents.items():
            path = folder / filename
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
            outputs[filename] = {"path": path.relative_to(self.root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return outputs

    def review(self, id_, reviewer, decision, note):
        if not self.config["reviewer"] or reviewer != self.config["reviewer"]:
            raise WorkflowError("A designated human reviewer is required; runner/model cannot approve")
        if decision not in {"approve", "reject"} or not note.strip():
            raise WorkflowError("Review decision and human rationale required")
        run = self.get_run(id_)
        if run["state"] != "draft_ready":
            raise WorkflowError("Only a current draft can be reviewed")
        for name in run["steps"]["artifacts"]:
            self.artifact(id_, name)
        run["review"] = {"reviewer": reviewer, "decision": decision, "note": note, "at": now(), "input_hash": run["input_hash"]}
        run["state"] = "approved" if decision == "approve" else "rejected"
        self.save_run(run)
        return run

    def publish(self, id_, public=False):
        run = self.get_run(id_)
        if run["state"] != "approved":
            raise WorkflowError("Publication requires a current approved run")
        if public:
            if any(ref["rights"]["redistribute"] is not True for e in run["steps"]["evidence"]["entries"] for ref in e["references"]):
                raise WorkflowError("Public redistribution not authorized for every source")
            raise WorkflowError("Public hosting is not implemented; internal release only")
        for name in run["steps"]["artifacts"]:
            self.artifact(id_, name)
        run["steps"]["release"] = self.render(run, "release")
        run["state"] = "published"
        self.save_run(run)
        return run

    def artifact(self, id_, name, release=False):
        run = self.get_run(id_)
        if run["state"] not in {"draft_ready", "approved", "published", "rejected"} or (release and run["state"] != "published"):
            raise WorkflowError("Artifact is unavailable or invalidated")
        artifact = run["steps"].get("release" if release else "artifacts", {}).get(name)
        if not artifact:
            raise WorkflowError("Unknown artifact")
        path = inside(self.root, artifact["path"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
            raise WorkflowError("Artifact integrity check failed")
        return path

    def withdraw(self, id_, reason):
        if not reason.strip():
            raise WorkflowError("Withdrawal reason required")
        source = next((a for a in self.records("asset") if a["id"] == id_), None)
        if not source:
            raise WorkflowError("Unknown source")
        body = dict(source["body"], withdrawal_reason=reason)
        return {"id": id_, "revision": self.put("asset", id_, body, "retired"), "state": "retired"}

    def backup(self):
        folder = inside(self.root, "outputs/backups")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (uuid.uuid4().hex + ".sqlite3")
        with closing(sqlite3.connect(path)) as backup:
            self.db.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise WorkflowError("Backup integrity check failed")
        return {"path": str(path), "note": "Database only; originals and artifacts must also be backed up"}

    def status(self):
        return {"domain": self.config["domain"], "backend": self.config["model"]["backend"],
                "model_live_verified": False, "assets": len(self.records("asset")), "units": len(self.records("unit")),
                "reviewer_configured": bool(self.config["reviewer"]),
                "runs": [dict(r) for r in self.db.execute("SELECT id,state,error FROM runs ORDER BY created")],
                "integrity": self.db.execute("PRAGMA integrity_check").fetchone()[0]}

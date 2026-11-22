"""Local curated knowledge retrieval and course validation. Uses Python stdlib only."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
LEGACY_CONFIG = json.loads((ROOT / "config/runtime.legacy.json").read_text(encoding="utf-8-sig"))
KB = ROOT / LEGACY_CONFIG["knowledge_directory"]
COURSE = ROOT / LEGACY_CONFIG["course_directory"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def search(query, limit=5):
    query = query.strip().lower()
    tokens = set(re.findall(r"[a-z0-9]+", query))
    for segment in re.findall(r"[\u4e00-\u9fff]+", query):
        tokens.update(segment[i:i+2] for i in range(len(segment)-1))
        if len(segment) == 1:
            tokens.add(segment)
    results = []
    sources = {s["id"]: s for s in read(KB / "sources.json")}
    for card in read(KB / "cards.json"):
        heading = (card["title"] + " " + " ".join(card["tags"])).lower()
        body = card["summary"].lower()
        score = sum(3 * (t in heading) + (t in body) for t in tokens)
        if query and query in heading:
            score += 6
        if score >= 3:
            result = dict(card, score=score, review_state="expert_review_pending")
            result["references"] = [dict(r, title=sources[r["source"]]["title"],
                                          url=sources[r["source"]]["url"],
                                          verification=sources[r["source"]]["verification"])
                                    for r in card["references"]]
            results.append(result)
    results.sort(key=lambda x: (-x["score"], x["id"]))
    return {"status": "evidence_candidates" if results else "no_evidence",
            "query": query, "method": "Chinese bigram / English keyword matching; not semantic QA",
            "results": results[:limit]}


def verify():
    sources = read(KB / "sources.json")
    cards = read(KB / "cards.json")
    course = read(COURSE / "curriculum.json")
    local = read(KB / "local_source_index.json")
    errors = []
    source_ids = {s["id"] for s in sources}
    card_ids = {c["id"] for c in cards}
    if len(source_ids) != len(sources) or len(card_ids) != len(cards):
        errors.append("Duplicate source/card IDs")
    for card in cards:
        if not card["references"]:
            errors.append(f"Missing references: {card['id']}")
        for ref in card["references"]:
            if ref["source"] not in source_ids:
                errors.append(f"Unknown source: {ref}")
            if ref["source"].startswith("LOCAL-") and ref["locator"] not in local[ref["source"]]["locators"]:
                errors.append(f"Invalid local coordinate: {ref}")
    lessons = course["lessons"]
    if len({l["id"] for l in lessons}) != len(lessons):
        errors.append("Duplicate lesson IDs")
    if sum(l["hours"] for l in lessons) != course["total_hours"] or course["total_hours"] != 48:
        errors.append("Course total must be 48")
    if not 40 <= course["total_hours"] <= 60:
        errors.append("Outside user requested hours")
    for key, expected in (("theory", course["theory_hours"]), ("practice", course["practice_hours"])):
        if sum(l[key] for l in lessons) != expected:
            errors.append(f"Wrong {key} total")
    for lesson in lessons:
        if lesson["theory"] + lesson["practice"] != lesson["hours"]:
            errors.append(f"Wrong lesson hours: {lesson['id']}")
        if not set(lesson["cards"]) <= card_ids:
            errors.append(f"Unknown card in {lesson['id']}")
        for field in ("objectives", "content", "practice_task", "deliverable", "assessment"):
            if not lesson.get(field):
                errors.append(f"Missing {field} in {lesson['id']}")
    for module in course["modules"]:
        if sum(l["hours"] for l in lessons if l["module"] == module["id"]) != module["hours"]:
            errors.append(f"Wrong module hours: {module['id']}")
    for asset in local.values():
        path = ROOT / asset["relative_path"]
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            errors.append(f"Original source changed: {path.name}")
    return {"status": "PASS" if not errors else "FAIL", "sources": len(sources),
            "international_sources": sum(not s["id"].startswith("LOCAL-") for s in sources),
            "cards": len(cards), "lessons": len(lessons), "hours": course["total_hours"],
            "errors": errors, "professional_review": "pending",
            "note": "Checks structure and source coordinates, not professional correctness or teaching effectiveness"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, choices=range(1, 11), default=5)
    lesson_parser = sub.add_parser("lesson")
    lesson_parser.add_argument("id")
    sub.add_parser("verify")
    args = parser.parse_args()
    if args.command == "search":
        result = search(args.query, args.limit)
    elif args.command == "lesson":
        result = next((l for l in read(COURSE / "curriculum.json")["lessons"] if l["id"] == args.id), None)
        if result is None:
            parser.error("Unknown lesson ID")
    else:
        result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if isinstance(result, dict) and result.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())

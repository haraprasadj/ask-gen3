"""Validate evals/questions.jsonl: schema, unique ids, and citations that resolve.

Run: uv run evals/check.py
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "evals" / "questions.jsonl"
INDEX = ROOT / "index.db"

REQUIRED = {"id", "question", "category", "must_include", "should_cite"}


def load() -> list[dict]:
    items = []
    for n, line in enumerate(QUESTIONS.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as e:
            sys.exit(f"{QUESTIONS}:{n}: bad JSON: {e}")
    return items


def main() -> None:
    items = load()
    errors = []

    seen = set()
    for item in items:
        missing = REQUIRED - item.keys()
        if missing:
            errors.append(f"{item.get('id', '?')}: missing keys {sorted(missing)}")
            continue
        if item["id"] in seen:
            errors.append(f"{item['id']}: duplicate id")
        seen.add(item["id"])
        if not item["question"].strip():
            errors.append(f"{item['id']}: empty question")
        for phrase in item["must_include"]:
            if phrase != phrase.lower():
                errors.append(f"{item['id']}: must_include {phrase!r} is not lowercase")
        # A scorable item needs something to score, unless it's a refusal case.
        if not item.get("expect_refusal") and not item["must_include"]:
            errors.append(f"{item['id']}: no must_include and not expect_refusal")
        if item.get("expect_refusal") and (item["must_include"] or item["should_cite"]):
            errors.append(f"{item['id']}: expect_refusal but has expectations")

    # Every cited path must actually be in the index, or the answer can never match.
    db = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)
    indexed = {
        f"{repo}/{path}" for repo, path in db.execute("select distinct repo, path from chunks")
    }
    db.close()
    for item in items:
        for cite in item.get("should_cite", []):
            if cite not in indexed:
                errors.append(f"{item['id']}: {cite} is not in the index")

    if errors:
        print("\n".join(errors))
        sys.exit(f"\n{len(errors)} problem(s) in {len(items)} items")
    print(f"ok: {len(items)} items, {sum(len(i['should_cite']) for i in items)} citations resolve")


if __name__ == "__main__":
    main()

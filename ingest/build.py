"""Build index.db from the uc-cdis corpus.

    uv run python -m ingest.build --out index.db
    uv run python -m ingest.build --only fence,indexd --out /tmp/small.db

Runs offline in CI (ADR-0005). The serving image never imports this module.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sqlite3
import struct
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime

import sqlite_vec
import yaml

from ingest.chunk import Chunk, chunk_file

HERE = pathlib.Path(__file__).parent
SCHEMA = HERE / "schema.sql"
CONFIG = HERE / "repos.yaml"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384
SCHEMA_VERSION = "1"
MAX_FILE_BYTES = 1_000_000

PROSE_ONLY = {".md", ".markdown", ".rst", ".txt"}
INDEXABLE = PROSE_ONLY | {
    ".py",
    ".go",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".sh",
    ".sql",
    ".java",
    ".r",
    ".tf",
    ".yaml",
    ".yml",
    ".json",
}


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


def github_repos(org: str) -> list[dict]:
    """Non-archived repos, from the API. Unauthenticated is enough at 60/hr."""
    out, page = [], 1
    token = os.environ.get("GITHUB_TOKEN")

    def req(url: str) -> urllib.request.Request:
        r = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "ask-gen3"},
        )
        if token:  # 5000/hr instead of 60 — CI sets this
            r.add_header("Authorization", f"Bearer {token}")
        return r

    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&page={page}"
        batch = json.loads(urllib.request.urlopen(req(url)).read())
        if not batch:
            return [b for b in out if not b["archived"]]
        out += batch
        page += 1


def clone(org: str, name: str, workdir: pathlib.Path) -> pathlib.Path | None:
    """Shallow clone, or reuse an existing checkout."""
    dest = workdir / name
    if dest.exists():
        return dest
    url = f"https://github.com/{org}/{name}.git"
    r = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", "--quiet", url, str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode:
        log(f"  ! clone failed {name}: {r.stderr.strip()[:120]}")
        shutil.rmtree(dest, ignore_errors=True)
        return None
    return dest


def head_sha(repo_dir: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def head_branch(repo_dir: pathlib.Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip() or "main"


def wanted_files(repo_dir: pathlib.Path, tier: int, exclude_paths: list[str]):
    """Yield (relative_path, extension) for files this tier indexes."""
    for path in repo_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(repo_dir).as_posix()
        if any(bad in "/" + rel for bad in exclude_paths):
            continue
        ext = path.suffix.lower()
        if ext not in INDEXABLE:
            continue
        if tier == 3:
            # README and docs/ only — enough to know the repo exists and why.
            head = rel.split("/")[0].lower()
            if ext not in PROSE_ONLY or not (head == "docs" or rel.lower().startswith("readme")):
                continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path, rel, ext


def parse_data(text: str) -> dict | None:
    try:
        doc = yaml.safe_load(text)  # also parses JSON
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


def chunk_repo(repo_dir: pathlib.Path, tier: int, exclude_paths: list[str]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, rel, ext in wanted_files(repo_dir, tier, exclude_paths):
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        doc = parse_data(text) if ext in {".yaml", ".yml", ".json"} else None
        try:
            chunks += chunk_file(rel, text, doc)
        except Exception as e:  # one malformed file must not kill a 20-minute build
            log(f"  ! chunk failed {rel}: {type(e).__name__}: {e}")
    return chunks


def embed_all(texts: list[str], batch: int = 256) -> list[bytes]:
    from fastembed import TextEmbedding  # noqa: PLC0415 — only needed for the embed phase

    model = TextEmbedding(model_name=MODEL)
    out: list[bytes] = []
    for i, v in enumerate(model.embed(texts, batch_size=batch)):
        out.append(struct.pack(f"{DIM}f", *v))
        if i and i % 5000 == 0:
            log(f"  embedded {i}/{len(texts)}")
    return out


def open_db(out: pathlib.Path) -> sqlite3.Connection:
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.executescript(SCHEMA.read_text())
    return db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="index.db", type=pathlib.Path)
    ap.add_argument("--workdir", default=pathlib.Path(".cache/repos"), type=pathlib.Path)
    ap.add_argument("--only", help="comma-separated repo names, for a fast partial build")
    ap.add_argument("--skip-tier3", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(CONFIG.read_text())
    org, excl_paths = cfg["org"], cfg["exclude_paths"]
    tiers: dict[str, int] = {}
    for name in cfg["tier1"]:
        tiers[name] = 1
    for name in cfg["tier2"]:
        tiers[name] = 2
    if not args.skip_tier3 and not args.only:
        known = set(tiers) | set(cfg["exclude"])
        for r in github_repos(org):
            if r["name"] not in known:
                tiers[r["name"]] = 3
    if args.only:
        keep = {n.strip() for n in args.only.split(",")}
        tiers = {n: tiers.get(n, 2) for n in keep}

    args.workdir.mkdir(parents=True, exist_ok=True)
    db = open_db(args.out)

    all_chunks: list[tuple[str, Chunk]] = []
    for i, (name, tier) in enumerate(sorted(tiers.items(), key=lambda kv: (kv[1], kv[0])), 1):
        log(f"[{i}/{len(tiers)}] tier{tier} {name}")
        repo_dir = clone(org, name, args.workdir)
        if repo_dir is None:
            continue
        chunks = chunk_repo(repo_dir, tier, excl_paths)
        if not chunks:
            log("  (nothing indexable)")
            continue
        full = f"{org}/{name}"
        db.execute(
            "insert into repos values (?,?,?,?,?,?)",
            (
                full,
                tier,
                head_sha(repo_dir),
                head_branch(repo_dir),
                None,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        all_chunks += [(full, c) for c in chunks]
        log(f"  {len(chunks)} chunks")

    if not all_chunks:
        log("nothing to index")
        return 1

    log(f"embedding {len(all_chunks)} chunks with {MODEL}")
    vectors = embed_all([c.text for _, c in all_chunks])

    for cid, ((repo, c), vec) in enumerate(zip(all_chunks, vectors, strict=True), start=1):
        db.execute(
            "insert into chunks values (?,?,?,?,?,?,?,?,?,?,?)",
            (
                cid,
                repo,
                c.path,
                c.start_line,
                c.end_line,
                c.kind,
                c.lang,
                c.heading,
                c.symbol,
                c.text,
                c.tokens,
            ),
        )
        db.execute("insert into chunks_vec values (?,?)", (cid, vec))

    db.execute(
        "insert into chunks_fts(rowid, text, heading, symbol, path) "
        "select id, text, heading, symbol, path from chunks"
    )
    db.execute("insert into chunks_fts(chunks_fts) values('optimize')")
    for k, v in {
        "schema_version": SCHEMA_VERSION,
        "embedding_model": MODEL,
        "embedding_dim": str(DIM),
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "chunk_count": str(len(all_chunks)),
    }.items():
        db.execute("insert into meta values (?,?)", (k, v))
    db.commit()
    db.execute("vacuum")
    db.execute("analyze")
    db.close()
    log(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.0f} MB, {len(all_chunks)} chunks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

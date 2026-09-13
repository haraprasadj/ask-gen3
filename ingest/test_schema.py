"""Self-check for index.db: schema applies, and hybrid retrieval actually fuses.

uv run ingest/test_schema.py
"""

import pathlib
import sqlite3
import struct

import sqlite_vec

SCHEMA = pathlib.Path(__file__).parent / "schema.sql"
DIM = 384


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.executescript(SCHEMA.read_text())
    return db


def vec(*head: float) -> bytes:
    """A DIM-length float32 vector from its first few components."""
    return struct.pack(f"{DIM}f", *head, *([0.0] * (DIM - len(head))))


def search(db, query: str, embedding: bytes, k: int = 3) -> list[int]:
    """Hybrid retrieval: BM25 and cosine, fused with RRF (k0=60)."""
    bm25 = [
        r[0]
        for r in db.execute(
            "select rowid from chunks_fts where chunks_fts match ? order by rank limit 20",
            (query,),
        )
    ]
    dense = [
        r[0]
        for r in db.execute(
            "select chunk_id from chunks_vec where embedding match ? and k = 20 order by distance",
            (embedding,),
        )
    ]
    scores: dict[int, float] = {}
    for ranking in (bm25, dense):
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)[:k]


def main() -> None:
    db = connect()
    db.execute(
        "insert into repos values ('uc-cdis/fence',2,'abc123','master','Apache-2.0','2026-09-13')"
    )
    rows = [
        # id, path, kind, lang, heading, symbol, text, embedding
        (
            1,
            "fence/blueprints/oauth2.py",
            "code",
            "python",
            None,
            "create_refresh_token",
            "def create_refresh_token(user, client): return jwt.encode(...)",
            vec(1.0, 0.0),
        ),
        (
            2,
            "docs/auth.md",
            "prose",
            "markdown",
            "Authentication > Refresh tokens",
            None,
            "Fence issues a refresh token that clients exchange for access tokens.",
            vec(0.9, 0.1),
        ),
        (
            3,
            "docs/storage.md",
            "prose",
            "markdown",
            "Storage > Buckets",
            None,
            "Configure bucket credentials for data upload and presigned_url access.",
            vec(0.0, 1.0),
        ),
    ]
    for cid, path, kind, lang, heading, symbol, text, emb in rows:
        db.execute(
            "insert into chunks values (?,'uc-cdis/fence',?,1,9,?,?,?,?,?,?)",
            (cid, path, kind, lang, heading, symbol, text, len(text.split())),
        )
        db.execute("insert into chunks_vec values (?,?)", (cid, emb))
    db.execute(
        "insert into chunks_fts(rowid, text, heading, symbol, path) "
        "select id, text, heading, symbol, path from chunks"
    )
    db.execute("insert into chunks_fts(chunks_fts) values('optimize')")

    # Identifiers survive tokenisation instead of splitting on '_' — the whole
    # reason BM25 is in the design (ADR-0006).
    assert [
        r[0]
        for r in db.execute("select rowid from chunks_fts where chunks_fts match 'presigned_url'")
    ] == [3]
    assert [
        r[0]
        for r in db.execute(
            "select rowid from chunks_fts where chunks_fts match 'create_refresh_token'"
        )
    ] == [1]

    # Lexical-only would miss the prose; dense-only would miss the exact symbol.
    # Fusion returns both, symbol first.
    assert search(db, "create_refresh_token", vec(0.95, 0.05))[:2] == [1, 2]

    # Metadata filtering and provenance survive the join.
    repo, sha, tier = db.execute(
        "select r.repo, r.commit_sha, r.tier from chunks c join repos r using (repo) where c.id = 1"
    ).fetchone()
    assert (repo, sha, tier) == ("uc-cdis/fence", "abc123", 2)

    # Constraints are real: kind is closed, line ranges ordered.
    for bad in (
        "insert into chunks values (9,'uc-cdis/fence','x',1,9,'notakind',null,null,null,'t',1)",
        "insert into chunks values (9,'uc-cdis/fence','x',9,1,'prose',null,null,null,'t',1)",
    ):
        try:
            db.execute(bad)
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError(f"constraint not enforced: {bad}")

    print("ok — schema, tokenisation, hybrid fusion, provenance, constraints")


if __name__ == "__main__":
    main()

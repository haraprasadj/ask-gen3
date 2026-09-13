"""Self-check for the agent's tools, on a synthetic index.

    uv run python -m server.test_retrieve

Uses a fake embedder so the check runs in milliseconds without the ONNX model;
what is under test is fusion, ranking and stitching, not the embeddings.
"""

from __future__ import annotations

import pathlib
import sqlite3
import struct
import tempfile

import sqlite_vec

from server import retrieve

DIM = retrieve.DIM
SCHEMA = pathlib.Path(__file__).parent.parent / "ingest" / "schema.sql"

# (id, repo, path, start, end, kind, symbol, text, vector-head)
ROWS = [
    (
        1,
        "uc-cdis/indexd",
        "indexd/index/blueprint.py",
        10,
        20,
        "code",
        "post_index",
        "def post_index(): create a new document with a did and a baseid",
        (1.0, 0.0),
    ),
    (
        2,
        "uc-cdis/indexd",
        "tests/test_index.py",
        1,
        5,
        "code",
        "test_post_index",
        "def test_post_index(): post_index did baseid post_index did baseid",
        (0.99, 0.0),
    ),
    (
        3,
        "uc-cdis/indexd",
        "docs/aliases.md",
        3,
        4,
        "prose",
        None,
        "An alias is a human readable name pointing at a baseid.",
        (0.0, 1.0),
    ),
    (
        4,
        "uc-cdis/fence",
        "fence/oauth.py",
        1,
        3,
        "code",
        "presigned",
        "def presigned(): return presigned_url for the object",
        (0.0, 0.0),
    ),
]
# open_file reassembly: two overlapping windows of one file.
STITCH = [
    (5, "uc-cdis/fence", "fence/long.py", 1, 3, "code", None, "alpha\nbravo\ncharlie", (0.0, 0.0)),
    (6, "uc-cdis/fence", "fence/long.py", 3, 5, "code", None, "charlie\ndelta\necho", (0.0, 0.0)),
    (7, "uc-cdis/fence", "fence/long.py", 9, 10, "code", None, "india\njuliet", (0.0, 0.0)),
]


def build(path: pathlib.Path, model: str = retrieve.MODEL) -> None:
    db = sqlite3.connect(path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.executescript(SCHEMA.read_text())
    for repo, tier in (("uc-cdis/indexd", 2), ("uc-cdis/fence", 2)):
        db.execute(
            "insert into repos values (?,?,?,?,?,?)",
            (repo, tier, "deadbeef", "master", None, "2026-09-13T00:00:00+00:00"),
        )
    for cid, repo, p, s, e, kind, sym, text, head in ROWS + STITCH:
        db.execute(
            "insert into chunks values (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, repo, p, s, e, kind, "python", None, sym, text, len(text) // 4),
        )
        db.execute(
            "insert into chunks_vec values (?,?)",
            (cid, struct.pack(f"{DIM}f", *head, *([0.0] * (DIM - len(head))))),
        )
    db.execute(
        "insert into chunks_fts(rowid, text, heading, symbol, path) "
        "select id, text, heading, symbol, path from chunks"
    )
    for k, v in (
        ("embedding_model", model),
        ("embedding_dim", str(DIM)),
        ("schema_version", "1"),
        ("built_at", "2026-09-13T00:00:00+00:00"),
    ):
        db.execute("insert into meta values (?,?)", (k, v))
    db.commit()
    db.close()


def use(path: pathlib.Path) -> None:
    retrieve.INDEX_PATH = path
    retrieve.db.cache_clear()


def fake_embed(head: tuple[float, ...]):
    return lambda _query: struct.pack(f"{DIM}f", *head, *([0.0] * (DIM - len(head))))


def main() -> None:
    tmp = pathlib.Path(tempfile.mkdtemp())
    index = tmp / "index.db"
    build(index)
    use(index)
    retrieve.embed_query = fake_embed((1.0, 0.0))

    # Implementation outranks its own test file, despite the test repeating
    # every query term more often — the demotion is what makes this true.
    hits = retrieve.search("post_index did baseid")
    top = [h.path for h in hits]
    assert top[0] == "indexd/index/blueprint.py", top
    assert retrieve.is_test_path("tests/test_index.py")
    assert not retrieve.is_test_path("indexd/index/blueprint.py")

    # Fusion, not either half alone: the prose chunk is lexically weak but
    # arrives via dense, and both sources are recorded.
    assert any(h.path == "docs/aliases.md" for h in hits), top
    assert {s for h in hits for s in h.sources} == {"bm25", "dense"}

    # One chunk per file.
    assert len(top) == len(set(top))

    # Filters.
    assert all(h.repo == "uc-cdis/fence" for h in retrieve.search("presigned", repo="fence"))

    # Citations carry the pinned commit, not a branch.
    hit = hits[0]
    assert hit.commit_sha == "deadbeef" and "blob/deadbeef" in hit.url
    assert hit.citation == "uc-cdis/indexd/indexd/index/blueprint.py#L10-L20"

    # grep: identifiers survive '_' tokenisation, and substrings fall back.
    assert [h.path for h in retrieve.grep("presigned_url")] == ["fence/oauth.py"]
    assert [h.path for h in retrieve.grep("resigned_ur")] == ["fence/oauth.py"]
    assert retrieve.grep("post_index", path_glob="tests/*") != []
    assert retrieve.grep("nothingmatchesthis") == []

    # FTS5 syntax in user input is data, not syntax.
    assert retrieve.search('alias OR "') is not None
    assert retrieve.grep("a AND (b") == []

    # open_file stitches overlapping windows losslessly and is honest about gaps.
    body = retrieve.open_file("fence", "fence/long.py", 1, 10)
    stitched = [line.split()[-1] for line in body.splitlines() if not line.startswith(".")]
    assert stitched == ["alpha", "bravo", "charlie", "delta", "echo", "india", "juliet"], stitched
    assert "lines 6-8 not indexed" in body
    assert "no indexed content" in retrieve.open_file("fence", "nope.py", 1, 5)

    assert "uc-cdis/indexd (tier 2" in retrieve.list_repos()
    assert retrieve.list_repos(filter="fence").count("\n") == 0

    # A model/index mismatch must fail loudly rather than retrieve badly.
    wrong = tmp / "wrong.db"
    build(wrong, model="some-other-model")
    use(wrong)
    try:
        retrieve.db()
    except RuntimeError as e:
        assert "some-other-model" in str(e)
    else:
        raise AssertionError("model mismatch was not rejected")

    print("ok — fusion, demotion, filters, grep, stitching, citations, model guard")


if __name__ == "__main__":
    main()

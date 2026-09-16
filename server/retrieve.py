"""The four agent tools, all backed by index.db and nothing else.

No network calls live here. That is not just tidiness: Cloud Run bills
wall-clock, so a round trip in the retrieval path is paid for twice (ADR-0008).
"""

from __future__ import annotations

import fnmatch
import functools
import os
import pathlib
import sqlite3
import struct
from dataclasses import dataclass, field

import sqlite_vec

INDEX_PATH = pathlib.Path(os.environ.get("INDEX_PATH", "index.db"))
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384
RRF_K = 60
CANDIDATES = 40

# Tests are worth indexing — they document real call signatures — but they
# swamp hybrid results for "how does X work" questions, because test files
# repeat the identifiers being asked about far more often than the one file
# that implements them. Demote rather than exclude.
TEST_PATH_MARKERS = ("/tests/", "tests/", "/test_", "test_", "_test.", "conftest", "/spec/")
TEST_PENALTY = 0.45


@dataclass
class Hit:
    chunk_id: int
    repo: str
    path: str
    start_line: int
    end_line: int
    kind: str
    heading: str | None
    symbol: str | None
    text: str
    commit_sha: str = ""
    score: float = 0.0
    sources: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        return f"{self.repo}/{self.path}#L{self.start_line}-L{self.end_line}"

    @property
    def url(self) -> str:
        return (
            f"https://github.com/{self.repo}/blob/{self.commit_sha}/{self.path}"
            f"#L{self.start_line}-L{self.end_line}"
        )

    def render(self) -> str:
        where = self.heading or self.symbol or self.path
        return f"[{self.citation}] {where}\n{self.text}"


@functools.cache
def db() -> sqlite3.Connection:
    """Read-only connection, opened once per execution environment."""
    if not INDEX_PATH.exists():
        raise FileNotFoundError(
            f"{INDEX_PATH} missing — build it with `just index` or set INDEX_PATH"
        )
    con = sqlite3.connect(f"file:{INDEX_PATH}?mode=ro", uri=True, check_same_thread=False)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    meta = dict(con.execute("select key, value from meta"))
    # A server whose embedding model disagrees with the index retrieves badly
    # and silently. Refuse to start instead (ADR-0002).
    if meta.get("embedding_model") != MODEL or int(meta.get("embedding_dim", 0)) != DIM:
        raise RuntimeError(
            f"index built with {meta.get('embedding_model')}/{meta.get('embedding_dim')}, "
            f"server expects {MODEL}/{DIM}"
        )
    return con


@functools.cache
def _embedder():
    """Loaded on first search, not at import: cold starts are billed too."""
    from fastembed import TextEmbedding  # noqa: PLC0415 — lazy: cold starts are billed

    return TextEmbedding(model_name=MODEL)


def embed_query(text: str) -> bytes:
    # bge models want an instruction prefix on the query side only; fastembed's
    # query_embed applies it. Using plain embed() here measurably hurts recall.
    vector = next(iter(_embedder().query_embed([text])))
    return struct.pack(f"{DIM}f", *vector)


def _fts_query(text: str) -> str:
    """FTS5 MATCH syntax is not user input syntax. Quote every token."""
    tokens = [
        t for t in "".join(c if c.isalnum() or c in "_.-/" else " " for c in text).split() if t
    ]
    return " OR ".join(f'"{t}"' for t in tokens[:24])


def search(query: str, repo: str | None = None, tier: int | None = None, k: int = 8) -> list[Hit]:
    """Hybrid retrieval: BM25 and cosine, fused with reciprocal rank."""
    con, where, params = db(), [], []
    if repo:
        where.append("c.repo like ?")
        params.append(f"%{repo}%")
    if tier:
        where.append("r.tier = ?")
        params.append(tier)
    filt = (" and " + " and ".join(where)) if where else ""

    lexical = (
        [
            r[0]
            for r in con.execute(
                "select c.id from chunks_fts f join chunks c on c.id = f.rowid "
                f"join repos r using (repo) where chunks_fts match ?{filt} "
                "order by rank limit ?",
                (_fts_query(query), *params, CANDIDATES),
            )
        ]
        if _fts_query(query)
        else []
    )

    dense = [
        r[0]
        for r in con.execute(
            "select v.chunk_id from chunks_vec v join chunks c on c.id = v.chunk_id "
            f"join repos r using (repo) where v.embedding match ? and k = ?{filt} "
            "order by distance",
            (embed_query(query), CANDIDATES, *params),
        )
    ]

    scores: dict[int, float] = {}
    origin: dict[int, list[str]] = {}
    for name, ranking in (("bm25", lexical), ("dense", dense)):
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            origin.setdefault(cid, []).append(name)

    hits = _load(sorted(scores, key=lambda c: scores[c], reverse=True))
    for h in hits:
        if is_test_path(h.path):
            scores[h.chunk_id] *= TEST_PENALTY
    hits.sort(key=lambda h: scores[h.chunk_id], reverse=True)
    # One chunk per file keeps eight slots showing eight different places.
    seen, out = set(), []
    for h in hits:
        if (h.repo, h.path) in seen:
            continue
        seen.add((h.repo, h.path))
        h.score, h.sources = scores[h.chunk_id], origin[h.chunk_id]
        out.append(h)
        if len(out) == k:
            break
    return out


def is_test_path(path: str) -> bool:
    lowered = "/" + path.lower()
    return any(m in lowered for m in TEST_PATH_MARKERS)


def _load(chunk_ids: list[int]) -> list[Hit]:
    if not chunk_ids:
        return []
    rows = (
        db()
        .execute(
            "select c.id, c.repo, c.path, c.start_line, c.end_line, c.kind, c.heading, "
            "c.symbol, c.text, r.commit_sha from chunks c join repos r using (repo) "
            f"where c.id in ({','.join('?' * len(chunk_ids))})",
            chunk_ids,
        )
        .fetchall()
    )
    by_id = {r[0]: Hit(*r) for r in rows}
    return [by_id[c] for c in chunk_ids if c in by_id]


def grep(
    pattern: str, repo: str | None = None, path_glob: str | None = None, k: int = 20
) -> list[Hit]:
    """Exact identifier lookup. FTS5 tokenises on '_' boundaries the way code
    needs (schema.sql), so `presigned_url` matches as one token."""
    con, params = db(), [f'"{pattern.strip()}"']
    sql = "select c.id from chunks_fts f join chunks c on c.id = f.rowid where chunks_fts match ?"
    if repo:
        sql += " and c.repo like ?"
        params.append(f"%{repo}%")
    sql += " order by rank limit ?"
    params.append(k * 3)
    try:
        ids = [r[0] for r in con.execute(sql, params)]
    except sqlite3.OperationalError:
        return []
    hits = _load(ids)
    if not hits:  # substring, not a token — brute force, ~200 ms at this size
        sql = "select id from chunks where text like ?"
        params = [f"%{pattern}%"]
        if repo:
            sql += " and repo like ?"
            params.append(f"%{repo}%")
        hits = _load([r[0] for r in con.execute(sql + " limit ?", (*params, k * 3))])
    if path_glob:
        hits = [h for h in hits if fnmatch.fnmatch(h.path, path_glob)]
    return hits[:k]


def open_file(repo: str, path: str, start: int = 1, end: int = 200) -> str:
    """Reassemble a line range from the chunks covering it.

    The index stores chunks, not files, and chunk line numbers are exact
    (ingest/test_chunk.py enforces it), so overlapping windows stitch back
    together losslessly. Ranges no chunk covers are reported, not invented.
    """
    rows = (
        db()
        .execute(
            "select start_line, end_line, text from chunks "
            "where repo like ? and path = ? and end_line >= ? and start_line <= ? "
            "order by start_line",
            (f"%{repo}%", path, start, end),
        )
        .fetchall()
    )
    if not rows:
        return f"no indexed content for {repo}/{path} lines {start}-{end}"
    lines: dict[int, str] = {}
    for s, _e, text in rows:
        for offset, line in enumerate(text.split("\n")):
            lines[s + offset] = line
    present = sorted(n for n in lines if start <= n <= end)
    if not present:
        return f"no indexed content for {repo}/{path} lines {start}-{end}"
    out, prev = [], None
    for n in present:
        if prev is not None and n > prev + 1:
            out.append(f"... lines {prev + 1}-{n - 1} not indexed ...")
        out.append(f"{n:5d}  {lines[n]}")
        prev = n
    return "\n".join(out)


def list_repos(filter: str | None = None) -> str:
    rows = (
        db()
        .execute(
            "select r.repo, r.tier, count(c.id) from repos r left join chunks c using (repo) "
            "group by r.repo order by r.tier, r.repo"
        )
        .fetchall()
    )
    if filter:
        rows = [r for r in rows if filter.lower() in r[0].lower()]
    return "\n".join(f"{repo} (tier {tier}, {n} chunks)" for repo, tier, n in rows[:80])


def tier_summary() -> str:
    rows = (
        db()
        .execute(
            "select r.tier, count(distinct r.repo), count(c.id) from repos r "
            "left join chunks c using (repo) group by r.tier order by r.tier"
        )
        .fetchall()
    )
    return "Indexed: " + "; ".join(
        f"tier {t}: {repos} repos, {chunks} chunks" for t, repos, chunks in rows
    )


def index_info() -> dict[str, str]:
    return dict(db().execute("select key, value from meta"))

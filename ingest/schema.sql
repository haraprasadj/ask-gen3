-- index.db — the entire retrieval layer (ADR-0001).
-- Built offline (ADR-0005), shipped inside the container image (ADR-0008),
-- opened read-only at serving time. Nothing writes to it in production.

pragma journal_mode = off;      -- never written after build
pragma page_size = 8192;

-- Build provenance. Asserted at server start: a server whose embedding model
-- disagrees with the index refuses to boot rather than retrieve badly (ADR-0002).
create table meta (
  key   text primary key,
  value text not null
) without rowid;
-- schema_version, embedding_model, embedding_dim, built_at, builder_commit,
-- chunk_count, corpus_bytes

-- One row per indexed repository, so every answer can cite the exact commit
-- it read rather than a moving branch.
create table repos (
  repo           text primary key,   -- 'uc-cdis/fence'
  tier           integer not null,   -- 1 docs, 2 core service, 3 readme-only
  commit_sha     text not null,
  default_branch text not null,
  license        text,               -- SPDX id from GitHub; unlicensed repos are not indexed
  indexed_at     text not null,      -- ISO 8601
  check (tier between 1 and 3)
) without rowid;

-- The unit of retrieval. Small and lossy on purpose: open_file() recovers
-- surrounding context on demand, which is cheaper than indexing large chunks.
create table chunks (
  id         integer primary key,
  repo       text    not null references repos(repo),
  path       text    not null,       -- repo-relative
  start_line integer not null,
  end_line   integer not null,
  kind       text    not null,       -- prose | code | openapi | schema
  lang       text,                   -- python, go, markdown, yaml, ...
  heading    text,                   -- 'Authentication > Refresh tokens'
  symbol     text,                   -- function or class name, code chunks only
  text       text    not null,
  tokens     integer not null,
  check (kind in ('prose','code','openapi','schema')),
  check (end_line >= start_line)
);

create index chunks_by_file on chunks(repo, path, start_line);
create index chunks_by_kind on chunks(kind, lang);

-- Lexical half of hybrid retrieval, and the engine behind the grep() tool.
-- External content: the FTS index points at chunks rather than copying text,
-- which is ~140 MB saved. Safe because the table is immutable after build,
-- so the usual synchronisation triggers are unnecessary.
--
-- tokenchars is load-bearing. Gen3 questions are full of identifiers, and
-- default unicode61 would split presigned_url into 'presigned' + 'url',
-- losing the exact match that BM25 is here to provide.
create virtual table chunks_fts using fts5(
  text, heading, symbol, path,
  content='chunks',
  content_rowid='id',
  tokenize="unicode61 tokenchars '_.-/'"
);

-- Dense half. 384 dimensions from bge-small-en-v1.5 (ADR-0002).
-- sqlite-vec scans brute force; at this corpus size that is single-digit ms.
create virtual table chunks_vec using vec0 (
  chunk_id  integer primary key,
  embedding float[384]
);

-- Populated after the bulk insert, in this order:
--   insert into chunks_fts(rowid, text, heading, symbol, path)
--     select id, text, heading, symbol, path from chunks;
--   insert into chunks_fts(chunks_fts) values('optimize');
--   vacuum; analyze;

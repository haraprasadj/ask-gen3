"""Format-aware chunking.

Chunks are deliberately small and lossy; the agent's open_file tool recovers
context on demand, which is cheaper than indexing large chunks for every query.
Line numbers are 1-based and inclusive, because they end up in citations.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

MAX_TOKENS = 1000
MIN_TOKENS = 40
WINDOW_LINES = 80
OVERLAP_LINES = 15

PROSE_EXT = {".md": "markdown", ".markdown": "markdown", ".rst": "rst", ".txt": "text"}
CODE_EXT = {
    ".py": "python",
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".sh": "shell",
    ".sql": "sql",
    ".java": "java",
    ".r": "r",
    ".tf": "terraform",
}
DATA_EXT = {".yaml": "yaml", ".yml": "yaml", ".json": "json"}

# Top-level definitions. Deliberately regex rather than tree-sitter: it covers
# the languages in this corpus, adds no dependency, and a missed symbol only
# means a chunk falls back to a line window.
SYMBOL_RE = {
    "python": re.compile(r"^(?:async\s+)?(?:def|class)\s+(\w+)"),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)|^type\s+(\w+)"),
    "javascript": re.compile(
        r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)|^(?:export\s+)?class\s+(\w+)"
        r"|^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\("
    ),
    "java": re.compile(r"^\s*(?:public|private|protected).*?\s(\w+)\s*\("),
    "sql": re.compile(
        r"^create\s+(?:table|view|index|function)\s+(?:if not exists\s+)?(\w+)", re.IGNORECASE
    ),
}
SYMBOL_RE["typescript"] = SYMBOL_RE["javascript"]


@dataclass(frozen=True)
class Chunk:
    path: str
    start_line: int
    end_line: int
    kind: str  # prose | code | openapi | schema
    lang: str | None
    heading: str | None
    symbol: str | None
    text: str

    @property
    def tokens(self) -> int:
        return est_tokens(self.text)


def est_tokens(text: str) -> int:
    """~4 characters per token. Only used for size caps, never for billing."""
    return max(1, len(text) // 4)


def _windows(lines: list[str], base: int) -> list[tuple[int, int, str]]:
    """Overlapping line windows as (start_line, end_line, text), 1-based."""
    if not lines:
        return []
    out, step = [], max(1, WINDOW_LINES - OVERLAP_LINES)
    for i in range(0, len(lines), step):
        part = lines[i : i + WINDOW_LINES]
        if not part:
            break
        out.append((base + i, base + i + len(part) - 1, "\n".join(part)))
        if i + WINDOW_LINES >= len(lines):
            break
    return out


def _split_oversized(text: str, start: int, make) -> list[Chunk]:
    """Emit `text` as one chunk, or as line windows if it exceeds MAX_TOKENS."""
    if est_tokens(text) <= MAX_TOKENS:
        lines = text.split("\n")
        return [make(start, start + len(lines) - 1, text)]
    return [make(s, e, t) for s, e, t in _windows(text.split("\n"), start)]


def chunk_prose(path: str, text: str, lang: str) -> list[Chunk]:
    """Split on headings, keeping the heading path in each chunk."""
    lines = text.split("\n")
    stack: list[tuple[int, str]] = []
    sections: list[tuple[int, str | None, list[str]]] = []
    cur: list[str] = []
    cur_start, cur_head = 1, None

    for i, line in enumerate(lines, start=1):
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            if cur and any(line.strip() for line in cur):
                sections.append((cur_start, cur_head, cur))
            level, title = len(m.group(1)), m.group(2).strip()
            stack = [(lv, t) for lv, t in stack if lv < level] + [(level, title)]
            cur_head = " > ".join(t for _, t in stack)
            cur, cur_start = [line], i
        else:
            cur.append(line)
    if cur and any(line.strip() for line in cur):
        sections.append((cur_start, cur_head, cur))

    # Merge runs of tiny sections so a heading with one line of text does not
    # become its own chunk.
    merged: list[tuple[int, str | None, list[str]]] = []
    for start, head, body in sections:
        if merged and est_tokens("\n".join(merged[-1][2])) < MIN_TOKENS:
            p_start, p_head, p_body = merged[-1]
            merged[-1] = (p_start, p_head, p_body + body)
        else:
            merged.append((start, head, body))

    out: list[Chunk] = []
    for start, head, body in merged:
        body_text = "\n".join(body).strip("\n")
        if not body_text.strip():
            continue
        out += _split_oversized(
            body_text,
            start,
            lambda s, e, t, h=head: Chunk(path, s, e, "prose", lang, h, None, t),
        )
    return out


def chunk_code(path: str, text: str, lang: str) -> list[Chunk]:
    """One chunk per top-level symbol, falling back to line windows."""
    lines = text.split("\n")
    pattern = SYMBOL_RE.get(lang)
    if pattern is None:
        return [Chunk(path, s, e, "code", lang, None, None, t) for s, e, t in _windows(lines, 1)]

    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        if line[:1].isspace():
            continue  # top level only
        m = pattern.match(line)
        if m:
            starts.append((i, next(g for g in m.groups() if g)))

    if not starts:
        return [Chunk(path, s, e, "code", lang, None, None, t) for s, e, t in _windows(lines, 1)]

    out: list[Chunk] = []
    if starts[0][0] > 1:  # imports and module docstring
        head = "\n".join(lines[: starts[0][0] - 1]).strip("\n")
        if head.strip():
            out += _split_oversized(
                head, 1, lambda s, e, t: Chunk(path, s, e, "code", lang, None, None, t)
            )
    for idx, (line_no, symbol) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else len(lines)
        body = "\n".join(lines[line_no - 1 : end]).strip("\n")
        if not body.strip():
            continue
        out += _split_oversized(
            body,
            line_no,
            lambda s, e, t, sym=symbol: Chunk(path, s, e, "code", lang, None, sym, t),
        )
    return out


def chunk_openapi(path: str, doc: dict, text: str) -> list[Chunk]:
    """One chunk per path+method. Line numbers are approximate: the operation is
    re-serialised, so citations point at the file, not an exact span."""
    out = []
    for route, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.startswith("x-") or not isinstance(op, dict):
                continue
            summary = " ".join(
                str(op.get(k, "")) for k in ("summary", "description", "operationId")
            ).strip()
            body = f"{method.upper()} {route}\n{summary}\n{json.dumps(op, indent=1)[:4000]}"
            out.append(
                Chunk(
                    path,
                    1,
                    text.count("\n") + 1,
                    "openapi",
                    "yaml",
                    f"{method.upper()} {route}",
                    op.get("operationId"),
                    body,
                )
            )
    return out


def chunk_schema(path: str, doc: dict, text: str) -> list[Chunk]:
    """One chunk per top-level definition of a Gen3 dictionary / JSON schema."""
    out = []
    props = doc.get("properties") if isinstance(doc.get("properties"), dict) else None
    node_id = doc.get("id") or doc.get("title")
    if props and node_id:
        desc = str(doc.get("description", ""))
        body = f"{node_id}\n{desc}\nproperties: {json.dumps(props, indent=1)[:4000]}"
        out.append(Chunk(path, 1, text.count("\n") + 1, "schema", "yaml", str(node_id), None, body))
    return out


def chunk_file(path: str, text: str, doc: dict | None = None) -> list[Chunk]:
    """Dispatch on extension and content. `doc` is the parsed YAML/JSON if any."""
    if "\x00" in text[:8000]:  # binary
        return []
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""

    if ext in PROSE_EXT:
        return chunk_prose(path, text, PROSE_EXT[ext])
    if ext in CODE_EXT:
        return chunk_code(path, text, CODE_EXT[ext])
    if ext in DATA_EXT and isinstance(doc, dict):
        if doc.get("openapi") or doc.get("swagger"):
            return chunk_openapi(path, doc, text)
        if doc.get("properties"):
            return chunk_schema(path, doc, text)
    return []

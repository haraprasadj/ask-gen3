"""Self-check for chunking.

    uv run python -m ingest.test_chunk

The load-bearing invariant is line fidelity: a chunk claiming L10-L20 must be
exactly those lines, because that range becomes a citation someone clicks.
"""

from ingest.chunk import MAX_TOKENS, Chunk, chunk_file, est_tokens

MD = """\
# Fence

Intro paragraph about the service.

## Authentication

How auth works in general, with enough words to clear the merge threshold
so that this section survives as a chunk of its very own without help.

### Refresh tokens

Fence issues a refresh token that clients exchange for access tokens, and
this sentence exists to pad the section past the minimum token count too.
"""

PY_SRC = """\
import os

from flask import Flask


def create_refresh_token(user, client):
    \"\"\"Issue a refresh token.\"\"\"
    return encode(user, client)


class TokenBlueprint:
    def revoke(self):
        return None
"""


def lines_of(text: str) -> list[str]:
    return text.split("\n")


def assert_line_fidelity(source: str, chunks: list[Chunk]) -> None:
    src = lines_of(source)
    for c in chunks:
        assert 1 <= c.start_line <= c.end_line <= len(src), f"bad range: {c}"
        expected = "\n".join(src[c.start_line - 1 : c.end_line]).strip("\n")
        assert c.text == expected, (
            f"{c.path} L{c.start_line}-{c.end_line} text does not match source lines"
        )


def test_prose() -> None:
    chunks = chunk_file("docs/fence.md", MD)
    assert_line_fidelity(MD, chunks)
    headings = [c.heading for c in chunks]
    assert "Fence > Authentication > Refresh tokens" in headings, headings
    assert all(c.kind == "prose" and c.lang == "markdown" for c in chunks)
    # Heading path is retrievable text, not just metadata.
    deepest = next(c for c in chunks if c.heading and c.heading.endswith("Refresh tokens"))
    assert "refresh token" in deepest.text.lower()


def test_code() -> None:
    chunks = chunk_file("fence/tokens.py", PY_SRC)
    assert_line_fidelity(PY_SRC, chunks)
    symbols = [c.symbol for c in chunks]
    assert "create_refresh_token" in symbols and "TokenBlueprint" in symbols, symbols
    # Imports become their own leading chunk rather than being lost.
    assert chunks[0].symbol is None and "import os" in chunks[0].text
    # A method is part of its class chunk, not a separate top-level symbol.
    cls = next(c for c in chunks if c.symbol == "TokenBlueprint")
    assert "def revoke" in cls.text


def test_oversized_is_windowed() -> None:
    big = "# Title\n\n" + "\n".join(f"line {i} of filler text here" for i in range(2000))
    chunks = chunk_file("docs/big.md", big)
    assert len(chunks) > 1
    assert all(c.tokens <= MAX_TOKENS * 1.1 for c in chunks), [c.tokens for c in chunks]
    assert_line_fidelity(big, chunks)
    # Windows overlap, so consecutive chunks share context across the seam.
    assert chunks[1].start_line <= chunks[0].end_line


def test_openapi() -> None:
    doc = {
        "openapi": "3.0.0",
        "paths": {
            "/user": {"get": {"operationId": "getUser", "summary": "Return the current user"}}
        },
    }
    chunks = chunk_file("openapis/swagger.yaml", "openapi: 3.0.0\npaths: {}\n", doc)
    assert len(chunks) == 1
    assert chunks[0].kind == "openapi"
    assert chunks[0].heading == "GET /user"
    assert "Return the current user" in chunks[0].text


def test_skips() -> None:
    assert chunk_file("logo.png", "\x00\x01binary") == []
    assert chunk_file("Makefile", "all:\n\tgo build\n") == []
    assert est_tokens("") == 1


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok — prose, code, windowing, openapi, line fidelity")


if __name__ == "__main__":
    main()

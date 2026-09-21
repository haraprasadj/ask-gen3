"""Self-check for the fetch egress boundary.

    uv run python -m server.test_web

No network: every test drives the policy functions or a fake transport. What is
under test is which URLs are reachable, because the model picks them and the
first interesting target on Cloud Run is the metadata server.
"""

from __future__ import annotations

import httpx

from server import web


def test_only_https_and_only_allowlisted_hosts() -> None:
    assert web.check("http://gen3.org/docs") == "only https URLs can be fetched"
    assert "not fetchable" in (web.check("https://evil.example.com/") or "")
    # A lookalike must not pass on a substring: only the exact host or a subdomain.
    assert "not fetchable" in (web.check("https://gen3.org.evil.com/") or "")
    assert "not fetchable" in (web.check("https://notgen3.org/") or "")
    assert web.host_allowed("docs.gen3.org") and web.host_allowed("gen3.org")
    assert not web.host_allowed("gen3.org.attacker.net")


def test_only_uc_cdis_repositories_on_the_github_hosts() -> None:
    """The host is not the author on GitHub: anyone can make a repository, and
    indexed repo content already reaches the model, so the path decides."""
    original = web.socket.getaddrinfo
    allow_all_dns()  # check() resolves the allowed ones; this module stays offline
    try:
        for url in [
            "https://github.com/uc-cdis/fence/blob/master/README.md",
            "https://github.com/UC-CDIS/fence",  # owners are case-insensitive
            "https://raw.githubusercontent.com/uc-cdis/fence/master/setup.py",
            # Hosts that carry no owner in the path are unaffected.
            "https://uc-cdis.github.io/gen3-docs/",
            "https://gen3.org/resources/user/",
        ]:
            assert web.check(url) is None, url
    finally:
        web.socket.getaddrinfo = original

    # Refusals return before any lookup.
    for url in [
        "https://github.com/attacker/payload",
        "https://github.com/uc-cdis-mirror/fence",  # whole segment, not a prefix
        "https://github.com/orgs/uc-cdis/repositories",
        "https://raw.githubusercontent.com/attacker/payload/main/instructions.md",
        "https://gist.github.com/attacker/deadbeef",
    ]:
        assert "uc-cdis repositories" in (web.check(url) or ""), url

    # Any user can put a file here by attaching it to an issue, and there is no
    # owner in the path to check, so the host is gone entirely.
    assert "not fetchable" in (
        web.check("https://user-content.githubusercontent.com/x/payload.md") or ""
    )


def test_private_and_metadata_addresses_are_refused() -> None:
    """The reason SSRF matters here, tested through the resolver directly."""
    for host, address in [
        ("metadata.google.internal", "169.254.169.254"),
        ("internal.example", "10.0.0.5"),
        ("localhost.example", "127.0.0.1"),
    ]:
        original = web.socket.getaddrinfo
        web.socket.getaddrinfo = lambda *a, _ip=address, **k: [
            (2, 1, 6, "", (_ip, 443)),
        ]
        try:
            assert "non-public" in (web.public_address(host) or ""), address
        finally:
            web.socket.getaddrinfo = original


def fake_client(responses: list[httpx.Response]) -> httpx.Client:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        response = queue.pop(0)
        response.request = request
        return response

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def allow_all_dns() -> None:
    web.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]


def test_a_redirect_off_the_allowlist_is_not_followed() -> None:
    """httpx would follow it; that is exactly how an allowed host becomes a
    hop to the metadata server."""
    original = web.socket.getaddrinfo
    allow_all_dns()
    try:
        client = fake_client(
            [httpx.Response(302, headers={"location": "https://169.254.169.254/token"})]
        )
        result = web.fetch("https://gen3.org/start", client=client)
        assert "cannot fetch" in result and "not fetchable" in result, result

        client = fake_client(
            [
                httpx.Response(302, headers={"location": "https://docs.gen3.org/real"}),
                httpx.Response(
                    200, headers={"content-type": "text/html"}, text="<p>Hello <b>docs</b></p>"
                ),
            ]
        )
        assert "Hello docs" in web.fetch("https://gen3.org/start", client=client)
    finally:
        web.socket.getaddrinfo = original


def test_body_is_capped_stripped_and_labelled_untrusted() -> None:
    original = web.socket.getaddrinfo
    allow_all_dns()
    try:
        page = "<script>alert(1)</script><h1>Title</h1><p>Body &amp; more</p>" + "x" * 50_000
        client = fake_client(
            [httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=page)]
        )
        result = web.fetch("https://docs.gen3.org/page", client=client)
        assert "untrusted content" in result, "fetched text was not labelled"
        assert "alert(1)" not in result, "script contents survived the strip"
        assert "Title" in result and "Body & more" in result
        # web.fetch caps bytes off the socket; the agent applies the token cap.
        assert len(result) <= len(web.BANNER) + web.MAX_BYTES

        # A binary or unexpected type is refused rather than fed to the model.
        client = fake_client(
            [httpx.Response(200, headers={"content-type": "image/png"}, content=b"")]
        )
        assert "not text" in web.fetch("https://docs.gen3.org/x.png", client=client)
    finally:
        web.socket.getaddrinfo = original


def main() -> None:
    original = web.socket.getaddrinfo
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_"):
                fn()
    finally:
        web.socket.getaddrinfo = original
    print("ok — scheme, allowlist, private addresses, redirects, body limits")


if __name__ == "__main__":
    main()

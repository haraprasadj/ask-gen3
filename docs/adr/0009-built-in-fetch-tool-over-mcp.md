# 9. A built-in fetch tool with a host allowlist, not an MCP server

Date: 2026-09-21

## Status

Accepted

## Context

Some Gen3 questions have no answer in the index. Release notes, the rendered
docs site and files in repositories outside the curated corpus all live on the
public web, and the weekly index build is a snapshot besides. The loop needs a
way to read a page.

The obvious off-the-shelf answer is the reference `fetch` MCP server: a
standard protocol, a maintained implementation, no fetching code to own.

The problem is not which implementation fetches the bytes. It is that the
model chooses the URL. A fetch tool reachable by prompt is a server-side
request forgery primitive, and on Cloud Run the first useful target is
`169.254.169.254`, which issues the service account token to anyone in the
instance who asks. Indexed repository content already reaches the model as
untrusted input ([SECURITY.md](../../SECURITY.md)), so "anyone who can land a
file in a public uc-cdis repository" is inside the threat model for choosing
that URL.

The reference fetch server fetches what it is asked to fetch. Restricting it
means configuration outside this repository, reviewed separately from the code
that depends on it.

## Decision

`fetch_url` is a fifth entry in `TOOLS`, implemented in `server/web.py` in
about a hundred lines, with the egress policy in the same file as the request:

- https only, and only hosts under `gen3.org`, `uc-cdis.github.io`,
  `github.com` or `raw.githubusercontent.com`
- on the GitHub hosts the first path segment must be `uc-cdis`, because the
  host says nothing about who wrote the content and anyone can create a
  repository; `githubusercontent.com` at large is excluded for the same reason,
  since any user can serve a file from it by attaching it to an issue
- redirects are never followed by the client; each hop is re-checked against
  the same policy
- every resolved address must be public, so an allowlisted name pointed at a
  private range is refused
- 300 kB, 10 seconds, text content types, tags stripped without a parser
  dependency
- results carry a banner marking them untrusted web content, and the system
  prompt tells the model to cite them as URLs rather than `[repo/path#L]`
  markers

No MCP client, no subprocess, no protocol layer. This continues
[ADR-0004](0004-no-agent-framework.md): the loop calls a Python function and
every token sent to the model stays visible in two files.

## Consequences

Adding a host is a one-line change in `server/web.py` and is the entire
security review for that host — which is the point, and also the risk, since a
one-line change is easy to wave through. `server/test_web.py` asserts the
refusals, so widening the allowlist without noticing requires editing a test
that says why.

The allowlist will be wrong sometimes: a question whose answer sits on a host
not in the list gets "must come from the index" rather than an answer. That is
the intended failure direction.

We own the fetching code, including its HTML handling. The tag strip is crude
and will mangle tables; a parser is another dependency to audit and was not
worth it for prose.

If this ever needs to reach arbitrary hosts, the answer is not a longer
allowlist. It is an egress proxy outside the process, and this ADR should be
superseded rather than edited.

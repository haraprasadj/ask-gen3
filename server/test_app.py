"""Self-check for the HTTP layer: the two ceilings and the two escapes.

    uv run python -m server.test_app

No index and no model. What is under test is the rate limit's idea of who the
caller is, and the renderer's idea of what a quote is — both of which are only
wrong once, and both of which cost real money or run real script when they are.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from types import SimpleNamespace as NS

from server import app as A


def request(xff: str | None = None, peer: str = "10.0.0.1", **extra: str):
    headers = dict(extra)
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return NS(headers=headers, client=NS(host=peer))


def test_client_ip_ignores_what_the_caller_claims() -> None:
    # Cloud Run appends the address it saw; everything left of it is hearsay.
    assert A.client_ip(request("1.2.3.4, 203.0.113.9")) == "203.0.113.9"
    assert A.client_ip(request("203.0.113.9")) == "203.0.113.9"
    # A forgeable header on its own must not become the identity.
    assert A.client_ip(request(None, **{"cf-connecting-ip": "1.2.3.4"})) == "10.0.0.1"
    assert A.client_ip(request("")) == "10.0.0.1"
    assert A.client_ip(request(None, peer="")) == "?"


def test_rate_limit_holds_and_eviction_spares_live_windows() -> None:
    A._buckets.clear()
    A._day.update(date="", count=0)
    for _ in range(A.RATE_LIMIT):
        assert A.allowed("198.51.100.7") is None
    assert "Rate limit" in (A.allowed("198.51.100.7") or "")

    # 10k throwaway keys must not reset the limit of a caller still in window.
    for n in range(10_001):
        A._buckets[f"pad-{n}"] = [time.time() - 7200]
    assert A.allowed("198.51.100.9") is None  # an allowed call is what triggers eviction
    assert len(A._buckets) <= 2, "expired windows were not evicted"
    assert A.allowed("198.51.100.7") is not None, "eviction cleared a live window"


def test_daily_cap_refuses() -> None:
    A._buckets.clear()
    A._day.update(date=time.strftime("%Y-%m-%d"), count=A.DAILY_QUESTION_CAP)
    assert "daily budget" in (A.allowed("198.51.100.8") or "")
    A._day.update(date="", count=0)


def test_page_carries_a_nonce_and_a_policy() -> None:
    response = A.home()
    body = response.body.decode()
    nonce = response.headers["content-security-policy"].split("'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">' in body and f'<style nonce="{nonce}">' in body
    assert "{{" not in body, "unsubstituted placeholder left in the page"
    policy = response.headers["content-security-policy"]
    assert A.home().headers["content-security-policy"] != policy, "nonce is not per-response"


# The renderer is JavaScript, so the only honest test of it runs JavaScript.
# Skipped rather than faked where node is absent; CI has it.
INJECTION = (
    '[uc-cdis/fence/a.py"onmouseover="alert`1`#L1-L2] '
    '[x](https://e.com"onfocus="alert`2`) '
    'https://e.com"onfocus="alert`3` '
    "<img src=x onerror=alert`4`>"
)


def test_renderer_cannot_break_out_of_an_attribute() -> None:
    if not shutil.which("node"):
        print("  (node not found — renderer check skipped)")
        return
    start = A.PAGE.index("const esc =")
    source = A.PAGE[start : A.PAGE.index("let streaming = false;")]
    script = f"{source}\nconsole.log(render({json.dumps(INJECTION)}));"
    node = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    html = node.stdout
    # A handler is only dangerous inside a tag. Escaped, these payloads sit in
    # text and in attribute values as &quot;-quoted noise, which is inert.
    for tag in re.findall(r"<[^>]*>", html):
        assert not re.search(r"\son[a-z]+\s*=", tag), f"handler escaped into {tag}: {html}"
    assert "<img" not in html and "<script" not in html, html
    assert 'href="https://github.com/uc-cdis/fence/' in html, "citations stopped rendering"


if __name__ == "__main__":
    test_client_ip_ignores_what_the_caller_claims()
    test_rate_limit_holds_and_eviction_spares_live_windows()
    test_daily_cap_refuses()
    test_page_carries_a_nonce_and_a_policy()
    test_renderer_cannot_break_out_of_an_attribute()
    print("ok — client identity, rate limits, nonce, renderer escaping")

"""HTTP layer: one page, one SSE endpoint.

Plain ASGI: uvicorn serves it locally and Cloud Run runs the same image with no
adapter, because Cloud Run streams server-sent events natively (ADR-0008).
"""

from __future__ import annotations

import json
import os
import secrets
import time
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from server import agent, retrieve

RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_HOUR", "20"))
DAILY_QUESTION_CAP = int(os.environ.get("DAILY_QUESTION_CAP", "2000"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "120"))

app = FastAPI(title="ask-gen3", docs_url=None, redoc_url=None)

# ponytail: per-instance counters. Cloud Run may run several instances, so the
# real ceilings are ~N x these. The hard global backstop is the spend limit on
# the OpenRouter key, which no amount of concurrency can exceed. Move to a
# shared store only if that backstop starts being reached.
_buckets: dict[str, list[float]] = {}
_day = {"date": "", "count": 0}


def client_ip(request: Request) -> str:
    """The IP the proxy observed, never the one the caller claims.

    Cloud Run appends the address it saw to X-Forwarded-For, so the rightmost
    entry is the only one a client cannot forge. Reading the leftmost entry —
    or any `cf-connecting-ip`-style header, with no Cloudflare in front of this
    — hands anyone an unlimited rate limit for a header they set themselves.
    """
    fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    return (fwd[-1] if fwd else "") or (request.client.host if request.client else "") or "?"


def allowed(ip: str) -> str | None:
    """Returns a refusal reason, or None to proceed."""
    today = time.strftime("%Y-%m-%d")
    if _day["date"] != today:
        _day.update(date=today, count=0)
    if _day["count"] >= DAILY_QUESTION_CAP:
        return "This instance has hit its daily budget. Try again tomorrow."
    now = time.time()
    hits = [t for t in _buckets.get(ip, []) if now - t < 3600]
    if len(hits) >= RATE_LIMIT:
        return f"Rate limit: {RATE_LIMIT} questions per hour. Try again later."
    hits.append(now)
    _buckets[ip] = hits
    if len(_buckets) > 10_000:
        # An unbounded dict is the other way to lose a machine. Drop only the
        # windows that have expired: clearing all of them would let anyone who
        # can mint 10k keys reset every real caller's limit too.
        for key in [k for k, v in _buckets.items() if not v or now - v[-1] >= 3600]:
            del _buckets[key]
    _day["count"] += 1
    return None


def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


# Cloud Run's frontend swallows /healthz before it reaches the container, so the
# same check is also served at /health. Locally, either works.
@app.get("/healthz")
@app.get("/health")
def healthz() -> dict:
    return {"ok": True, **retrieve.index_info()}


# Not the context policy — agent.history_messages decides what the model sees.
# This is the ceiling on what a caller can make this process parse at all.
MAX_HISTORY_MESSAGES = 100


def parse_history(raw: object) -> list[dict]:
    """The body is caller-controlled: take only well-formed user and assistant
    text. A forged `system` turn is dropped here rather than handed to the model.
    """
    if not isinstance(raw, list):
        return []
    return [
        {"role": m["role"], "content": m["content"]}
        for m in raw[-MAX_HISTORY_MESSAGES:]
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
    ]


# POST, not GET: a conversation's history does not fit in a URL, which is also
# why the page uses fetch rather than EventSource.
@app.post("/ask")
async def ask(request: Request) -> StreamingResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    q = body.get("q") if isinstance(body.get("q"), str) else ""
    history = parse_history(body.get("history"))

    def stream():
        refusal = allowed(client_ip(request))
        if refusal:
            yield sse("error", {"text": refusal})
            return
        started = time.time()
        try:
            for event in agent.answer(q, history=history):
                if time.time() - started > REQUEST_TIMEOUT:
                    yield sse("error", {"text": "Took too long. Try a narrower question."})
                    return
                if event.kind == "token":
                    yield sse("token", {"text": event.text})
                elif event.kind == "tool":
                    yield sse("tool", {"name": event.text, "args": event.data.get("args", {})})
                elif event.kind == "answer":
                    yield sse(
                        "answer",
                        {
                            "text": event.text,
                            **event.data,
                            "seconds": round(time.time() - started, 1),
                        },
                    )
                elif event.kind == "error":
                    yield sse("error", {"text": event.text})
        except Exception as e:  # never leak a stack trace to the page
            # Type and location, never the message: it can carry the question
            # or an upstream response body (SECURITY.md).
            where = traceback.extract_tb(e.__traceback__)[-1]
            print(f"unhandled: {type(e).__name__} at {where.filename}:{where.lineno}", flush=True)
            yield sse("error", {"text": "Something broke on our side."})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def home() -> HTMLResponse:
    try:
        info = retrieve.index_info()
        built = info.get("built_at", "?")[:10]
        chunks = f"{int(info.get('chunk_count', 0)):,}"
        repos = retrieve.db().execute("select count(*) from repos").fetchone()[0]
        footer = f"{chunks} chunks from {repos} uc-cdis repositories, indexed {built}"
    except Exception:
        footer = "index unavailable"
    # Second layer under the escaping in render(): even a renderer bug cannot
    # run injected script without this nonce, which changes every response.
    nonce = secrets.token_urlsafe(16)
    page = PAGE.replace("{{logo}}", LOGO).replace("{{footer}}", footer).replace("{{nonce}}", nonce)
    return HTMLResponse(
        page,
        headers={
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            # Cloud Run terminates TLS but does not send this for you.
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        },
    )


# The Gen3 wordmark, from uc-cdis/data-portal src/img/icons/gen3.svg (Apache-2.0).
# Inlined so the page is still one request; letters use currentColor so the mark
# works on both themes.
LOGO = r"""<svg class="logo" viewBox="0 0 195 89" role="img" aria-label="Gen3">
<defs><polygon id="lp" points="0 0.0686 41.8857 0.0686 41.8857 69.0836 0 69.0836"/>
<mask id="lm" fill="#fff"><use xlink:href="#lp" xmlns:xlink="http://www.w3.org/1999/xlink"/></mask></defs>
<path fill="#3283c8" d="m 194.7646,61.4042 c 0,15.197 -12.205,27.527 -27.396,27.527 h -0.125 c -15.2,0 -27.405,-12.33 -27.405,-27.527 0,-2.242 1.867,-4.107 4.108,-4.107 2.242,0 4.115,1.865 4.115,4.107 0,10.715 8.592,19.305 19.307,19.305 10.584,0 19.181,-8.59 19.181,-19.305 0,-10.215 -9.471,-19.303 -20.428,-19.303 -1.498,0 -2.994,-0.873 -3.613,-2.246 -0.752,-1.367 -0.627,-2.986 0.252,-4.232 l 20.049,-27.403 h -38.863 c -2.241,0 -4.108,-1.869 -4.108,-4.113 0,-2.24 1.867,-4.107 4.108,-4.107 h 46.836 c 1.498,0 2.986,0.871 3.613,2.242 0.744,1.367 0.619,2.986 -0.25,4.232 l -20.68,28.399 c 12.088,3.117 21.299,14.076 21.299,26.531"/>
<path fill="currentColor" mask="url(#lm)" transform="translate(0,-0.0684)" d="m 41.8857,37.8886 v 26.189 c 0,1.702 -1.422,3.12 -3.123,3.12 -1.797,0 -3.119,-1.418 -3.119,-3.12 v -1.043 c -3.779,3.688 -8.981,6.049 -14.749,6.049 -11.441,0 -20.895,-9.357 -20.895,-20.892 v -27.133 c 0,-11.535 9.454,-20.99 20.895,-20.99 11.534,0 20.991,9.455 20.991,20.99 0,1.701 -1.422,3.119 -3.123,3.119 -1.797,0 -3.119,-1.418 -3.119,-3.119 0,-8.133 -6.619,-14.75 -14.749,-14.75 -8.037,0 -14.655,6.617 -14.655,14.75 v 27.133 c 0,8.035 6.618,14.654 14.655,14.654 8.13,0 14.749,-6.619 14.749,-14.654 v -7.184 h -13.803 c -1.701,0 -3.119,-1.418 -3.119,-3.119 0,-1.703 1.418,-3.123 3.119,-3.123 h 16.922 c 1.701,0 3.123,1.42 3.123,3.123"/>
<path fill="currentColor" d="m 57.4873,37.3456 v 23.543 h 22.129 c 1.697,0 3.117,1.418 3.117,3.121 0,1.701 -1.42,3.119 -3.117,3.119 h -25.25 c -1.795,0 -3.119,-1.418 -3.119,-3.119 V 4.5366 c 0,-1.701 1.324,-3.117 3.119,-3.117 h 25.25 c 1.697,0 3.117,1.416 3.117,3.117 0,1.704 -1.42,3.122 -3.117,3.122 h -22.129 v 23.447 h 22.129 c 1.697,0 3.117,1.42 3.117,3.121 0,1.795 -1.42,3.119 -3.117,3.119 z"/>
<path fill="currentColor" d="m 122.5401,65.5331 -24.016,-48.031 v 46.613 c 0,1.793 -1.418,3.119 -3.119,3.119 -1.703,0 -3.121,-1.326 -3.121,-3.119 v -59.85 c 0,-1.418 0.945,-2.74 2.363,-3.025 1.42,-0.379 2.932,0.285 3.498,1.607 l 24.11,48.125 V 4.2651 c 0,-1.703 1.324,-3.121 3.121,-3.121 1.699,0 3.117,1.418 3.117,3.121 v 59.85 c 0,1.512 -1.039,2.74 -2.457,3.119 h -0.66 c -1.227,0 -2.268,-0.568 -2.836,-1.701"/>
</svg>"""

PAGE = r"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ask gen3 — questions about the Gen3 platform, answered from source</title>
<meta name="description" content="Ask questions about the Gen3 platform and get answers cited to the uc-cdis source.">
<style nonce="{{nonce}}">
  :root { color-scheme: light dark;
          --fg: #10141b; --bg: #fbfbfa; --panel: #fff; --dim: #667085;
          --line: #e4e4e0; --accent: #1a56b8; --brand: #3283c8; --code: #f4f4f1;
          --shadow: 0 1px 2px rgba(16,20,27,.06), 0 8px 24px rgba(16,20,27,.06); }
  @media (prefers-color-scheme: dark) {
    :root { --fg: #e7e9ee; --bg: #101318; --panel: #171b22; --dim: #98a2b3;
            --line: #272c35; --accent: #7aa7f0; --brand: #58a6e8; --code: #1b1f27;
            --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3); }
  }
  * { box-sizing: border-box; }
  body { margin: 0 auto; padding: 3rem 1.25rem 4rem; max-width: 48rem;
         background: var(--bg); color: var(--fg); font-size: 16px; line-height: 1.7;
         font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
         -webkit-font-smoothing: antialiased; }
  header { display: flex; align-items: center; gap: .85rem; margin-bottom: .6rem; }
  .logo { height: 30px; width: auto; display: block; color: var(--fg); }
  header .bar { width: 1px; height: 26px; background: var(--line); }
  h1 { font-size: 1.05rem; font-weight: 600; margin: 0; letter-spacing: .02em;
       text-transform: lowercase; color: var(--dim); }
  p.sub { color: var(--dim); margin: 0 0 1.75rem; font-size: .95rem; max-width: 34rem; }
  .composer { position: sticky; bottom: 0; background: var(--bg); padding: .9rem 0 1rem;
              margin-top: 2rem; }
  form { display: flex; gap: .5rem; background: var(--panel); padding: .45rem;
         border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow); }
  form:focus-within { border-color: var(--brand); }
  textarea { flex: 1; min-width: 0; padding: .6rem .75rem; font: inherit; color: var(--fg);
             background: none; border: 0; resize: none; field-sizing: content;
             max-height: 17rem; overflow-y: auto; }
  textarea:focus { outline: none; }
  form button { align-self: flex-end; }
  button { padding: .6rem 1.25rem; font: inherit; font-weight: 550; border-radius: 10px;
           cursor: pointer; border: 1px solid transparent; background: var(--accent);
           color: #fff; transition: opacity .15s; }
  button:hover { opacity: .88; }
  button[disabled] { opacity: .5; cursor: default; }
  button.stop { background: none; color: var(--dim); border-color: var(--line); }
  button.stop:hover { color: #b42318; border-color: #b42318; opacity: 1; }
  .examples { margin: .75rem 0 0; padding: 0; list-style: none; display: flex;
              flex-wrap: wrap; gap: .45rem; }
  .examples button { background: none; color: var(--dim); border: 1px solid var(--line);
                     font-size: .85rem; font-weight: 400; padding: .35rem .7rem;
                     border-radius: 999px; }
  .examples button:hover { color: var(--fg); border-color: var(--dim); opacity: 1; }
  #activity:not(:empty) { margin-top: 2rem; padding-left: 1rem; font-size: .85rem;
                          color: var(--dim); border-left: 2px solid var(--line); }
  #activity div { padding: .1rem 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                  font-size: .8rem; }
  #activity div.live::after { content: " ."; animation: dots 1.2s steps(4, end) infinite; }
  @keyframes dots { 0% { content: " ."; } 33% { content: " .."; } 66% { content: " ..."; } }
  #out { margin-top: 1.75rem; word-wrap: break-word; }
  #out > :first-child { margin-top: 0; }
  .turn + .turn { margin-top: 2rem; padding-top: 1.75rem; border-top: 1px solid var(--line); }
  .ask { margin: 0 0 1rem; font-weight: 550; }
  .ask::before { content: "> "; color: var(--dim); }
  #new[hidden], .examples[hidden] { display: none; }
  #new { margin-top: .75rem; background: none; color: var(--dim); border: 1px solid var(--line);
         font-size: .85rem; font-weight: 400; padding: .35rem .7rem; border-radius: 999px; }
  #new:hover { color: var(--fg); border-color: var(--dim); opacity: 1; }
  #out p { margin: 0 0 1rem; }
  #out h2 { font-size: 1.1rem; margin: 1.8rem 0 .6rem; }
  #out h3, #out h4 { font-size: .98rem; margin: 1.4rem 0 .5rem; }
  #out ul, #out ol { margin: 0 0 1rem; padding-left: 1.3rem; }
  #out li { margin: .2rem 0; }
  #out blockquote { margin: 0 0 1rem; padding: .1rem 0 .1rem 1rem;
                    border-left: 3px solid var(--line); color: var(--dim); }
  #out code { background: var(--code); padding: .1em .35em; border-radius: 5px;
              font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .87em; }
  #out pre { background: var(--code); padding: .85rem 1rem; border-radius: 10px;
             overflow-x: auto; white-space: pre; font-size: .85rem; line-height: 1.55;
             border: 1px solid var(--line); }
  #out pre code { background: none; padding: 0; font-size: inherit; }
  #out table { border-collapse: collapse; width: 100%; margin: 0 0 1rem; font-size: .9rem;
               display: block; overflow-x: auto; }
  #out th, #out td { border: 1px solid var(--line); padding: .4rem .6rem; text-align: left;
                     vertical-align: top; }
  #out th { background: var(--code); font-weight: 600; }
  #out hr { border: 0; border-top: 1px solid var(--line); margin: 1.5rem 0; }
  a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
  cite { font-style: normal; }
  cite a { font-size: .78em; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
           background: var(--code); border: 1px solid var(--line); border-radius: 5px;
           padding: .05em .35em; text-decoration: none; white-space: nowrap; }
  cite a:hover { border-color: var(--accent); }
  .meta { color: var(--dim); font-size: .8rem; margin-top: 1.75rem;
          border-top: 1px solid var(--line); padding-top: .6rem; }
  footer { margin-top: 3.5rem; padding-top: 1rem; border-top: 1px solid var(--line);
           color: var(--dim); font-size: .8rem; line-height: 1.6; }
  .err { color: #b42318; }
  @media (prefers-color-scheme: dark) { .err { color: #f97066; } }
</style>
<header>
  {{logo}}
  <span class="bar"></span>
  <h1>ask gen3</h1>
</header>
<p class="sub">Questions about the Gen3 platform, answered from the uc-cdis source
with citations to the exact lines.</p>
<div id="out"></div>
<div id="activity"></div>
<div class="composer">
  <form id="f">
    <textarea id="q" name="q" rows="1" placeholder="How does fence issue a refresh token?"
              autocomplete="off" maxlength="2000" autofocus></textarea>
    <button id="go">Ask</button>
  </form>
  <ul class="examples" id="examples">
    <li><button type="button">What is indexd and what does it store?</button></li>
    <li><button type="button">How do presigned URLs get authorized?</button></li>
    <li><button type="button">What does a Gen3 data dictionary node look like?</button></li>
  </ul>
  <button type="button" id="new" hidden>New conversation</button>
</div>
<footer>{{footer}} &middot; answers can be wrong &mdash; follow the citations
&middot; <a href="https://github.com/uc-cdis">uc-cdis</a><br>
Your question is sent to <a href="https://openrouter.ai" target="_blank" rel="noopener">OpenRouter</a>
and the model it routes to. Don't put anything confidential in it.<br>
A community project. Not affiliated with or endorsed by the Gen3 team or the
Center for Translational Data Science; the Gen3 logo is used to identify the
software this tool indexes.</footer>
<script nonce="{{nonce}}">
const f = document.getElementById('f'), q = document.getElementById('q');
const out = document.getElementById('out'), act = document.getElementById('activity');
const go = document.getElementById('go'), newBtn = document.getElementById('new');
const examples = document.getElementById('examples');

document.querySelectorAll('.examples button').forEach(b =>
  b.onclick = () => { q.value = b.textContent; f.requestSubmit(); });

// Enter sends, Shift+Enter is a newline. isComposing keeps an IME's confirming
// Enter from sending a half-typed question.
q.onkeydown = e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); f.requestSubmit(); }
};

// Quotes included: inline() puts this text inside href="...", and a browser
// treats href="x"onclick="y" as two attributes, so an unescaped quote is script
// execution. Indexed repository content reaches here through the model.
const esc = s => s.replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Every indexed repo is uc-cdis, so the org is optional in the marker and always
// in the URL: small models drop it, and [fence/fence/x.py] must not become
// github.com/fence/fence.
const CITE = /\[(?:uc-cdis\/)?([\w.-]+)\/([^\]\s]+?)#L(\d+)-L(\d+)\]/g;

// Everything below runs on already-escaped text, so no raw HTML can get through.
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    // Some models wrap the citation in a markdown link; drop their URL and let
    // CITE build the canonical one, or the bare-URL rule renders it twice.
    .replace(/\[([\w.-]+\/[^\]\s]+?#L\d+-L\d+)\]\(https?:[^)\s]+\)/g, '[$1]')
    .replace(CITE, (m, repo, path, a, b) =>
      `<cite><a target="_blank" rel="noopener" href="https://github.com/uc-cdis/${repo}/blob/HEAD/${path}#L${a}-L${b}">${path}:${a}</a></cite>`)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a target="_blank" rel="noopener" href="$2">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      (m, pre, url) => `${pre}<a target="_blank" rel="noopener" href="${url}">${url}</a>`);
}

const cells = row => row.trim().replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()));

// Enough markdown for what the model emits. Tolerates an unterminated fence,
// because this is called on every token while the answer is still streaming.
function render(text) {
  const src = esc(text).split('\n');
  let html = '', para = [], list = '', i = 0;
  const endPara = () => { if (para.length) { html += '<p>' + inline(para.join(' ')) + '</p>'; para = []; } };
  const endList = () => { if (list) { html += '</' + list + '>'; list = ''; } };
  const flush = () => { endPara(); endList(); };

  while (i < src.length) {
    const line = src[i], t = line.trim();
    let m;
    if (t.startsWith('```')) {
      flush();
      const body = [];
      for (i++; i < src.length && !src[i].trim().startsWith('```'); i++) body.push(src[i]);
      i++;
      html += '<pre><code>' + body.join('\n') + '</code></pre>';
    } else if (/^\|.*\|$/.test(t) && /^\|[\s:|-]+\|$/.test((src[i + 1] || '').trim())) {
      flush();
      html += '<table><thead><tr>' + cells(t).map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>';
      for (i += 2; i < src.length && src[i].trim().startsWith('|'); i++)
        html += '<tr>' + cells(src[i]).map(c => '<td>' + c + '</td>').join('') + '</tr>';
      html += '</tbody></table>';
    } else if ((m = t.match(/^(#{1,6})\s+(.*)$/))) {
      flush();
      const h = Math.min(m[1].length + 1, 4);
      html += `<h${h}>` + inline(m[2]) + `</h${h}>`;
      i++;
    } else if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
      flush(); html += '<hr>'; i++;
    } else if (t.startsWith('&gt;')) {  // '>' is already escaped by the time we parse
      flush();
      const body = [];
      for (; i < src.length && /^\s*&gt;/.test(src[i]); i++) body.push(src[i].replace(/^\s*&gt;\s?/, ''));
      html += '<blockquote>' + inline(body.join(' ')) + '</blockquote>';
    } else if ((m = t.match(/^([-*+]|\d+[.)])\s+(.*)$/))) {
      endPara();
      const want = /^\d/.test(m[1]) ? 'ol' : 'ul';
      if (list !== want) { endList(); html += '<' + want + '>'; list = want; }
      html += '<li>' + inline(m[2]) + '</li>';
      i++;
    } else if (!t) {
      flush(); i++;
    } else {
      endList(); para.push(t); i++;
    }
  }
  flush();
  return html;
}

let streaming = false;
let history = [];   // prior turns, text only — the server keeps nothing
let current = null; // the answer element being streamed into
let controller = null;

function reset() {
  streaming = false;
  go.textContent = 'Ask'; go.classList.remove('stop');
  act.querySelectorAll('.live').forEach(e => e.classList.remove('live'));
  newBtn.hidden = history.length === 0;
  examples.hidden = history.length > 0 || out.children.length > 0;
}

function note(text, cls) {
  const el = document.createElement('p');
  el.className = cls; el.textContent = text;
  (current || out).appendChild(el);
}

function stop() {
  if (!streaming) return;
  controller.abort();
  note('Stopped.', 'meta');
  reset();
}

newBtn.onclick = () => {
  if (streaming) stop();
  history = []; out.innerHTML = ''; act.innerHTML = '';
  newBtn.hidden = true; examples.hidden = false; q.focus();
};

// The button doubles as the stop control, so preventDefault here is what keeps
// the click from submitting the form it lives in.
go.onclick = e => { if (streaming) { e.preventDefault(); stop(); } };
addEventListener('keydown', e => { if (e.key === 'Escape') stop(); });

// EventSource cannot POST, and the history does not fit in a URL, so the SSE
// frames are parsed by hand: split on the blank line, read event: and data:.
async function readStream(body, onEvent) {
  const res = await fetch('/ask', {
    method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify(body), signal: controller.signal,
  });
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '', cut;
  for (;;) {
    const {value, done} = await reader.read();
    if (done) break;
    buffer += value;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const kind = /^event: (.*)$/m.exec(frame), data = /^data: (.*)$/m.exec(frame);
      if (kind && data) onEvent(kind[1], JSON.parse(data[1]));
    }
  }
}

f.onsubmit = async e => {
  e.preventDefault();
  const question = q.value.trim();
  if (!question || streaming) return;
  q.value = ''; act.innerHTML = '';

  const turn = document.createElement('section');
  turn.className = 'turn';
  const asked = document.createElement('p');
  asked.className = 'ask'; asked.textContent = question;
  const answer = document.createElement('div');
  turn.append(asked, answer);
  out.appendChild(turn);
  current = answer;
  examples.hidden = true;
  asked.scrollIntoView({block: 'start', behavior: 'smooth'});

  streaming = true; go.textContent = 'Stop'; go.classList.add('stop');
  controller = new AbortController();
  let buffer = '';
  const step = text => {
    act.querySelectorAll('.live').forEach(e => e.classList.remove('live'));
    const line = document.createElement('div');
    line.className = 'live'; line.textContent = text;
    act.appendChild(line);
  };
  step('searching the index');

  try {
    await readStream({q: question, history}, (kind, d) => {
      if (kind === 'tool') {
        const arg = d.args.query || d.args.pattern || d.args.path || d.args.filter || '';
        step(`${d.name}(${arg})`);
      } else if (kind === 'token') {
        buffer += d.text; answer.innerHTML = render(buffer);
      } else if (kind === 'answer') {
        answer.innerHTML = render(d.text);
        const meta = document.createElement('div');
        meta.className = 'meta';
        meta.textContent = `${d.steps} steps · ${d.seconds}s · ${d.usage.prompt_tokens + d.usage.completion_tokens} tokens`;
        answer.appendChild(meta);
        // Only a completed answer joins the history; a stopped or failed turn
        // would otherwise leave the model following a half-sentence.
        history.push({role: 'user', content: question}, {role: 'assistant', content: d.text});
      } else if (kind === 'error') {
        note(d.text, 'err');
      }
    });
  } catch (err) {
    if (err.name !== 'AbortError') note('Connection lost.', 'err');
  }
  reset();
};
</script>
</html>
"""

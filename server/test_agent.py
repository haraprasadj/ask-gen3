"""Self-check for the agent loop, against a scripted fake model.

    uv run python -m server.test_agent

No network and no index: what is under test is delta accumulation, tool
dispatch and the ceilings, all of which are easy to get quietly wrong.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from server import agent


def delta(content=None, tool_calls=None, usage=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls or []))], usage=usage)


def call_delta(index, cid=None, name=None, args=None):
    return NS(index=index, id=cid, type="function", function=NS(name=name, arguments=args))


class FakeClient:
    """Replays scripted turns; records the messages it was sent."""

    def __init__(self, turns):
        self.turns, self.seen = list(turns), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.seen.append(kw)
        return iter(self.turns.pop(0) if self.turns else [delta(content="fallback")])


def test_tool_call_split_across_deltas() -> None:
    """Name and arguments arrive in fragments; they must reassemble."""
    turns = [
        [
            delta(content="Looking. "),
            delta(tool_calls=[call_delta(0, "c1", "gr", '{"pat')]),
            delta(tool_calls=[call_delta(0, None, "ep", 'tern": "presigned_url"}')]),
            delta(usage=NS(prompt_tokens=100, completion_tokens=5)),
        ],
        [
            delta(content="Found it [uc-cdis/fence/f.py#L1-L2]."),
            delta(usage=NS(prompt_tokens=400, completion_tokens=20)),
        ],
    ]
    client = FakeClient(turns)
    captured = {}
    agent.run_tool = lambda name, args: (
        captured.update(name=name, args=args) or ("[uc-cdis/fence/f.py#L1-L2] hit", [])
    )

    events = list(agent.answer("how do presigned urls work?", client))
    kinds = [e.kind for e in events]
    assert captured == {"name": "grep", "args": {"pattern": "presigned_url"}}, captured
    assert "tool" in kinds and kinds[-1] == "answer", kinds
    assert "".join(e.text for e in events if e.kind == "token").startswith("Looking.")

    final = events[-1]
    assert final.text == "Found it [uc-cdis/fence/f.py#L1-L2]."
    assert final.data["steps"] == 2
    assert final.data["usage"]["completion_tokens"] == 25

    # The tool result was actually fed back to the model.
    tool_msgs = [m for m in client.seen[-1]["messages"] if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"


def test_ceiling_forces_an_answer() -> None:
    """A model that only ever calls tools must still terminate, and the final
    turn must be offered no tools so it cannot call another."""
    loop = [[delta(tool_calls=[call_delta(0, "c", "search", '{"query": "x"}')])]] * 20
    client = FakeClient(loop)
    agent.run_tool = lambda name, args: ("no results", [])

    events = list(agent.answer("q", client))
    assert len(client.seen) == agent.MAX_STEPS, len(client.seen)
    assert client.seen[-1]["tools"] is None
    assert events[-1].kind == "error"


def test_malformed_arguments_do_not_crash() -> None:
    client = FakeClient(
        [
            [delta(tool_calls=[call_delta(0, "c", "search", "{not json")])],
            [delta(content="done")],
        ]
    )
    seen = {}
    agent.run_tool = lambda name, args: seen.update(args=args) or ("ok", [])
    events = list(agent.answer("q", client))
    assert seen["args"] == {}
    assert events[-1].kind == "answer"


def test_tool_exception_is_reported_to_the_model() -> None:
    def boom(name, args):
        raise ValueError("index exploded")

    agent.run_tool = boom
    client = FakeClient(
        [
            [delta(tool_calls=[call_delta(0, "c", "search", "{}")])],
            [delta(content="recovered")],
        ]
    )
    events = list(agent.answer("q", client))
    assert events[-1].kind == "answer"
    tool_msg = next(m for m in client.seen[-1]["messages"] if m["role"] == "tool")
    assert "ValueError" in tool_msg["content"] and "index exploded" in tool_msg["content"]


def test_input_limits() -> None:
    assert next(iter(agent.answer("   ", FakeClient([])))).kind == "error"
    client = FakeClient([[delta(content="hi")]])
    list(agent.answer("x" * 5000, client))
    asked = client.seen[0]["messages"][-1]["content"]
    assert len(asked) == agent.MAX_QUESTION_CHARS


def test_model_failure_is_caught() -> None:
    class Broken(FakeClient):
        def _create(self, **kw):
            raise RuntimeError("502 from upstream")

    events = list(agent.answer("q", Broken([])))
    assert events[-1].kind == "error" and "RuntimeError" in events[-1].text


def main() -> None:
    original = agent.run_tool
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_"):
                fn()
    finally:
        agent.run_tool = original
    print("ok — delta reassembly, ceilings, bad args, tool errors, input limits")


if __name__ == "__main__":
    main()

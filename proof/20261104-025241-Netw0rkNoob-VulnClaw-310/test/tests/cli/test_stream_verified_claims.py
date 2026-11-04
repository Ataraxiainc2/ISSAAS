"""Regression test for https://github.com/Netw0rkNoob/VulnClaw/issues/310.

``AgentState.get_summary()`` reports ``verified_claims`` as an int count, but
the ``--stream`` teardown in ``run``/``solve`` sliced it like a list, so any
successful run (>= 1 verified claim) crashed with
``TypeError: 'int' object is not subscriptable`` before emitting ``complete``.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from typer.testing import CliRunner


def _drive(coro):
    """Drive an await-chain with no real async work, without an event loop.

    The faked CLI orchestration below never touches the network or threads, so
    awaiting it needs no socket-backed event loop. If the chain ever awaits a
    real future, fail loudly instead of hanging.
    """

    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    raise AssertionError("unexpected real await in faked CLI orchestration")


def _summary_with_verified_claim() -> dict:
    from vulnclaw.agent.agent_state import AgentState

    state = AgentState(origin="test", goal="probe")
    state.record_verified_claim("SQL injection confirmed", ["e001"])
    state.completed = True
    summary = state.get_summary()
    # Pin the real contract: get_summary() returns a count, not a list.
    assert summary["verified_claims"] == 1
    return summary


def _events(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def _fake_config(monkeypatch):
    import vulnclaw.cli.main as cli_main
    from vulnclaw.config.schema import VulnClawConfig

    config = VulnClawConfig()
    config.llm.api_key = "test-key"
    config.session.engine = "solve"
    config.session.show_thinking = True
    monkeypatch.setattr(cli_main, "load_config", lambda: config)
    monkeypatch.setattr(asyncio, "run", _drive)
    return config


def test_run_stream_complete_with_verified_claim_count(monkeypatch):
    import vulnclaw.cli.main as cli_main

    config = _fake_config(monkeypatch)
    summary = _summary_with_verified_claim()

    class FakeAgent:
        def __init__(self):
            self.config = config
            self.session_state = SimpleNamespace(findings=[])
            self.context = SimpleNamespace(
                state=SimpleNamespace(
                    agent_state=SimpleNamespace(get_summary=lambda: summary)
                )
            )

        async def solve(self, prompt, **kwargs):
            kwargs["on_event"]("completed", {})
            return []

    async def fake_orchestrated(*, runner, **kwargs):
        await runner(FakeAgent(), config)
        return SimpleNamespace(summary={"findings_count": 0})

    monkeypatch.setattr(cli_main, "_run_cli_orchestrated_task", fake_orchestrated)

    result = CliRunner().invoke(
        cli_main.app,
        ["run", "example.com", "--stream", "--engine", "solve"],
    )

    assert result.exit_code == 0, result.exception
    events = _events(result.output)
    complete = next(event for event in events if event["type"] == "complete")
    assert complete["result"]["findings"] == []
    assert "goal reached" in complete["summary"]


def test_solve_stream_complete_with_verified_claim_count(monkeypatch):
    import vulnclaw.cli.main as cli_main

    config = _fake_config(monkeypatch)
    summary = _summary_with_verified_claim()

    class FakeAgent:
        def __init__(self):
            self.config = config
            self.context = SimpleNamespace(
                state=SimpleNamespace(
                    agent_state=SimpleNamespace(get_summary=lambda: summary)
                )
            )

        async def solve(self, prompt, **kwargs):
            kwargs["on_event"]("completed", {})
            return []

    async def fake_orchestrated(*, runner, **kwargs):
        await runner(FakeAgent(), config)
        return SimpleNamespace(summary={"findings_count": 0})

    monkeypatch.setattr(cli_main, "_run_cli_orchestrated_task", fake_orchestrated)

    result = CliRunner().invoke(
        cli_main.app,
        ["solve", "example.com", "--goal", "probe", "--stream"],
    )

    assert result.exit_code == 0, result.exception
    events = _events(result.output)
    complete = next(event for event in events if event["type"] == "complete")
    assert complete["result"]["findings"] == []
    assert "goal reached" in complete["summary"]

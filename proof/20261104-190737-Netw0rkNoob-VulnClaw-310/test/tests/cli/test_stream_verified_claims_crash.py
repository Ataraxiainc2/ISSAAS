"""Regression test for https://github.com/Netw0rkNoob/VulnClaw/issues/310.

``AgentState.get_summary()`` reports ``verified_claims`` as an ``int`` count,
not a list. The ``--stream`` teardown in both ``run`` and ``solve`` sliced that
value (``(summary.get("verified_claims") or [])[:50]``), so any successful run
with >= 1 verified claim crashed with ``TypeError: 'int' object is not
subscriptable`` after all evidence was already on disk, and the terminal
``complete`` event was never emitted.

These tests drive the real CLI ``--stream`` teardown with a *real*
``AgentState.get_summary()`` payload (int count of 1) instead of a hand-built
list, so they fail with the reported ``TypeError`` on the unfixed code.

Note: ``asyncio.run`` is stubbed with a loop-free coroutine driver because the
whole fake agent chain below never awaits a real Future; only the synchronous
teardown code under test needs to execute.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner


def _real_summary_with_one_verified_claim() -> dict:
    """Build a genuine ``get_summary()`` payload with >= 1 verified claim."""
    from vulnclaw.agent.agent_state import AgentState

    state = AgentState()
    state.record_verified_claim("SQL injection confirmed", [])
    state.mark_complete("goal reached")
    summary = state.get_summary()
    # Premise of the bug: the summary carries an int count, not a claim list.
    assert summary["verified_claims"] == 1
    return summary


def _json_events(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def _drive_without_loop(coro):
    """Run a coroutine to completion without an event loop.

    Only works when nothing in the chain awaits a real Future, which holds
    for the fake agents below (all awaits are bare coroutine chains).
    """
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    raise AssertionError("coroutine unexpectedly needs a real event loop")


def _install_stream_fakes(monkeypatch, cli_main, summary: dict) -> None:
    """Route the CLI's orchestrated task through a fake agent using ``summary``."""
    import asyncio

    from vulnclaw.config.schema import VulnClawConfig

    config = VulnClawConfig()
    config.llm.api_key = "test-key"
    config.session.engine = "solve"
    config.session.show_thinking = True
    monkeypatch.setattr(cli_main, "load_config", lambda: config)
    monkeypatch.setattr(asyncio, "run", _drive_without_loop)

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
            sink = kwargs["stream_sink"]
            sink.on_status("working")
            sink.on_stream_end()
            kwargs["on_event"]("completed", {})
            return []

    async def fake_orchestrated(*, runner, **kwargs):
        await runner(FakeAgent(), config)
        return SimpleNamespace(summary={"findings_count": 0})

    monkeypatch.setattr(cli_main, "_run_cli_orchestrated_task", fake_orchestrated)


def test_run_stream_survives_verified_claim_count(monkeypatch):
    import vulnclaw.cli.main as cli_main

    _install_stream_fakes(monkeypatch, cli_main, _real_summary_with_one_verified_claim())

    result = CliRunner().invoke(
        cli_main.app,
        ["run", "example.com", "--stream", "--engine", "solve"],
    )

    assert result.exit_code == 0, (
        f"run --stream crashed with verified_claims=1: {result.exception!r}"
    )
    events = _json_events(result.output)
    complete = next(event for event in events if event.get("type") == "complete")
    assert complete["result"]["findings"] == []


def test_solve_stream_survives_verified_claim_count(monkeypatch):
    import vulnclaw.cli.main as cli_main

    _install_stream_fakes(monkeypatch, cli_main, _real_summary_with_one_verified_claim())

    result = CliRunner().invoke(
        cli_main.app,
        ["solve", "example.com", "--goal", "probe", "--max-steps", "2",
         "--no-resume", "--stream"],
    )

    assert result.exit_code == 0, (
        f"solve --stream crashed with verified_claims=1: {result.exception!r}"
    )
    events = _json_events(result.output)
    complete = next(event for event in events if event.get("type") == "complete")
    assert complete["result"]["findings"] == []

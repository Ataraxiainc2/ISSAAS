"""Offline tests for the Muse console cost math (Prompt 22).

No network: drives ``tools/console/server.py`` cost computation and
``run.usage`` parsing against a fake runs directory.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_server():
    path = Path(__file__).resolve().parents[1] / "tools" / "console" / "server.py"
    spec = importlib.util.spec_from_file_location("muse_console_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_run(runs: Path, stem: str, models: list[dict]) -> None:
    (runs / f"{stem}.meta.json").write_text(
        json.dumps({"run": stem}), encoding="utf-8"
    )
    (runs / f"{stem}.jsonl").write_text(
        json.dumps(
            {"payload_type": "run.usage", "payload": {"models": models}}
        )
        + "\n",
        encoding="utf-8",
    )


def test_console_cost_bills_reasoning_at_output_rate() -> None:
    server = _load_server()
    without = server.cost("muse-spark-1.3", 1000, 200, 50)
    with_reasoning = server.cost("muse-spark-1.3", 1000, 200, 50, 500)
    assert with_reasoning == pytest.approx(without + 500 * 4.25 / 1e6)
    assert server.cost(
        "muse-spark-1.3-contributor", 1000, 200, 50, 500
    ) == pytest.approx(((1000 - 200) * 0.10 + 200 * 0.002 + 550 * 0.20) / 1e6)
    assert server.cost("no-such-model", 1, 0, 1) is None


def test_console_parse_run_costs_reasoning_tokens(tmp_path, monkeypatch) -> None:
    server = _load_server()
    runs = tmp_path / "muse-runs"
    runs.mkdir()
    _write_run(
        runs,
        "demo-20261103-120000",
        [{"model": "muse-spark-1.3", "input_tokens": 1000,
          "cached_tokens": 200, "output_tokens": 50,
          "reasoning_tokens": 500, "calls": 1}],
    )
    monkeypatch.setattr(server, "RUNS", runs)
    (entry,) = server.parse_run("demo-20261103-120000")["usage"]
    assert entry["reasoning_tokens"] == 500
    assert entry["cost_usd"] == pytest.approx(
        ((1000 - 200) * 1.25 + 200 * 0.15 + 550 * 4.25) / 1e6
    )


def test_console_old_usage_without_reasoning_counts_zero(
    tmp_path, monkeypatch
) -> None:
    server = _load_server()
    runs = tmp_path / "muse-runs"
    runs.mkdir()
    _write_run(
        runs,
        "old-20261103-120000",
        [{"model": "muse-spark-1.3", "input_tokens": 1000, "output_tokens": 50}],
    )
    monkeypatch.setattr(server, "RUNS", runs)
    (entry,) = server.parse_run("old-20261103-120000")["usage"]
    assert entry["cost_usd"] == pytest.approx(
        server.cost("muse-spark-1.3", 1000, 0, 50)
    )

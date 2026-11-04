"""Offline tests for the panel's pure route helpers (no browser needed).

``parseRoute`` and ``formatCallCount`` are extracted from ``web/app.js``
and executed in node; the test asserts the deep-link parse table and the
call-count plural. Skipped when node is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "web" / "app.js"


def _extract_function(source: str, name: str) -> str:
    start = source.find(f"function {name}(")
    assert start != -1, f"{name} not found in web/app.js"
    depth = 0
    for i in range(source.find("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _run_helpers() -> list:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    source = APP_JS.read_text(encoding="utf-8")
    driver = (
        'const TABS = ["runs", "scout", "triage", "detail", "actions"];\n'
        + _extract_function(source, "parseRoute")
        + "\n"
        + _extract_function(source, "formatCallCount")
        + "\n"
        + "const out = [\n"
        + '  parseRoute("#detail/20261102-1"),\n'
        + '  parseRoute("#runs"),\n'
        + '  parseRoute(""),\n'
        + '  parseRoute("#nope/x"),\n'
        + '  parseRoute("#triage/t1"),\n'
        + '  parseRoute("#detail/a%2Fb"),\n'
        + "  formatCallCount(1),\n"
        + "  formatCallCount(0),\n"
        + "  formatCallCount(12),\n"
        + "];\n"
        + "console.log(JSON.stringify(out));"
    )
    proc = subprocess.run(
        [node, "-e", driver],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_panel_route_helpers_parse_and_pluralize() -> None:
    (detail, runs, empty, unknown, triage, encoded,
     one, zero, many) = _run_helpers()
    # The deep link keeps its run id; the initial route wins.
    assert detail == {"tab": "detail", "id": "20261102-1"}
    assert triage == {"tab": "triage", "id": "t1"}
    assert encoded == {"tab": "detail", "id": "a/b"}
    # Empty and unknown tabs fall back to runs (id preserved when present).
    assert runs == {"tab": "runs", "id": ""}
    assert empty == {"tab": "runs", "id": ""}
    assert unknown == {"tab": "runs", "id": "x"}
    # Call counts pluralize: "1 call", otherwise "N calls".
    assert one == "1 call"
    assert zero == "0 calls"
    assert many == "12 calls"

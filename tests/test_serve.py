"""Offline tests for ``issa serve`` (Prompt 15b).

The sandbox forbids listening sockets, so these tests drive the real
:class:`PanelServer` request handlers over a fake transport (no sockets at
all): :func:`call` builds a ``PanelHandler`` with in-memory ``rfile``/``wfile``
and runs the same ``do_GET``/``do_POST`` dispatch a live server would use.
Stub action runners replace every CLI call, so no model, GitHub or other
network call is ever made.

Covers the panel contract: loopback bind wiring, foreign Host/Origin
rejection, confirm-token gating of paid actions, the one-at-a-time busy lock,
run-id validation, no secrets in JSON, and the ``recommended_max_usd`` rule.
"""

from __future__ import annotations

import io
import json
import threading
import time
from email.message import Message
from pathlib import Path

import pytest

from issa_as import select as select_mod
from issa_as import serve as serve_mod
from issa_as import traces as trace_log
from issa_as.cli import build_parser, main

ISSUE_URL = "https://github.com/o/r/issues/1"
LOCAL_HOST = "127.0.0.1:8780"


def call(server, method, path, body=None, headers=None, raw=None):
    """Run one request through the real handler; returns (status, doc, raw)."""
    payload = raw if raw is not None else (
        json.dumps(body).encode("utf-8") if body is not None else b""
    )
    msg = Message()
    msg["Host"] = LOCAL_HOST
    for key, value in (headers or {}).items():
        del msg[key]
        msg[key] = value
    if payload and raw is None:
        msg["Content-Type"] = "application/json"
    msg["Content-Length"] = str(len(payload))
    handler = serve_mod.PanelHandler.__new__(serve_mod.PanelHandler)
    handler.server = server
    handler.command = method
    handler.path = path
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.headers = msg
    handler.rfile = io.BytesIO(payload)
    handler.wfile = io.BytesIO()
    dispatch = getattr(handler, f"do_{method}")
    dispatch()
    head, _, data = handler.wfile.getvalue().partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    try:
        doc = json.loads(data.decode("utf-8")) if data else None
    except ValueError:
        doc = None
    return status, doc, data


def wait_job(server, job_id, timeout_s=15.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, job, _ = call(server, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.fixture()
def panel(tmp_path):
    """Panel state with fast stub runners (no CLI, no sockets, no network)."""
    root = tmp_path / "panelroot"
    (root / "web").mkdir(parents=True)
    (root / "web" / "index.html").write_text(
        "<html><body>panel</body></html>", encoding="utf-8"
    )
    calls: list[tuple[str, dict]] = []
    gate = threading.Event()
    gate.set()

    def fast_stub(server, params):
        calls.append(("fast", dict(params)))
        return {"ok": True, "params": dict(params)}

    def blocking_stub(server, params):
        calls.append(("blocking", dict(params)))
        gate.wait(15.0)
        return {"ok": True}

    server = serve_mod.PanelServer(
        ("127.0.0.1", 0), root, bind_and_activate=False
    )
    server.runners.update(
        {
            "scout": fast_stub,
            "triage": fast_stub,
            "solve": fast_stub,
            "recover": fast_stub,
            "reverify": fast_stub,
            "run": fast_stub,
        }
    )
    yield {
        "server": server,
        "root": root,
        "calls": calls,
        "gate": gate,
        "blocking_stub": blocking_stub,
    }
    server.server_close()


def _write_scout_report(root: Path, run_id: str = "s1") -> None:
    scout_dir = root / "scout"
    scout_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": run_id,
        "rate_limited": False,
        "candidates": [
            {
                "issue_url": ISSUE_URL,
                "repo": "o/r",
                "number": 1,
                "title": "Crash on empty input",
                "labels": ["bug"],
                "security_class": None,
                "runner": "python",
                "stars": 120,
                "has_snippet": True,
                "signals": {"has_repro_steps": 1.0},
                "score": 0.7,
                "body_excerpt": "Steps to reproduce: run it.",
            }
        ],
    }
    (scout_dir / f"{run_id}.json").write_text(json.dumps(report), encoding="utf-8")


# -- bind + guards --------------------------------------------------------


def test_binds_loopback_only(panel, monkeypatch) -> None:
    assert serve_mod.BIND_HOST == "127.0.0.1"
    seen: dict = {}

    class FakeServer:
        def __init__(self, address, root):
            seen["address"] = address
            self.server_address = address

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    status, doc, _ = call(panel["server"], "GET", "/api/status")
    assert status == 200
    assert doc["busy"] is False
    monkeypatch.setattr(serve_mod, "PanelServer", FakeServer)
    assert serve_mod.serve(port=9999, root=panel["root"]) == 0
    assert seen["address"] == ("127.0.0.1", 9999)


def test_serve_cli_defaults_to_loopback_port() -> None:
    args = build_parser().parse_args(["serve"])
    assert args.command == "serve"
    assert args.port == 8780
    args = build_parser().parse_args(["serve", "--port", "9999"])
    assert args.port == 9999


def test_serve_cli_invokes_panel(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    seen: dict = {}

    def fake_serve(port=8780, root=None):
        seen["port"] = port
        return 0

    monkeypatch.setattr(serve_mod, "serve", fake_serve)
    assert main(["serve", "--port", "9999"]) == 0
    assert seen["port"] == 9999


def test_foreign_host_rejected(panel) -> None:
    status, doc, _ = call(
        panel["server"], "GET", "/api/overview", headers={"Host": "evil.example"}
    )
    assert status == 403
    assert "localhost" in doc["error"]


def test_foreign_origin_and_referer_rejected(panel) -> None:
    server = panel["server"]
    status, _, _ = call(
        server, "GET", "/api/overview", headers={"Origin": "http://evil.example"}
    )
    assert status == 403
    status, _, _ = call(
        server, "GET", "/api/overview", headers={"Referer": "http://evil.example/x"}
    )
    assert status == 403
    status, _, _ = call(
        server, "GET", "/api/overview", headers={"Origin": "http://127.0.0.1:9999"}
    )
    assert status == 200
    status, _, _ = call(server, "GET", "/api/overview")
    assert status == 200


def test_reads_are_get_only(panel) -> None:
    server = panel["server"]
    status, _, _ = call(server, "POST", "/api/overview", body={})
    assert status == 404
    status, _, _ = call(server, "PUT", "/api/overview")
    assert status == 405
    status, _, _ = call(server, "DELETE", "/api/runs")
    assert status == 405


# -- paid actions need a valid confirm token ------------------------------


def test_paid_action_refused_without_token(panel) -> None:
    server = panel["server"]
    status, doc, _ = call(
        server,
        "POST",
        "/api/actions/solve",
        body={"issue_url": ISSUE_URL, "model": "m", "effort": "medium"},
    )
    assert status == 403
    assert "confirm token" in doc["error"]
    status, _, _ = call(
        server,
        "POST",
        "/api/actions/solve",
        body={
            "issue_url": ISSUE_URL,
            "model": "m",
            "effort": "medium",
            "confirm_token": "bogus",
        },
    )
    assert status == 403


def test_estimate_token_bound_to_params_and_single_use(panel) -> None:
    server = panel["server"]
    status, estimate, _ = call(
        server,
        "POST",
        "/api/estimate/solve",
        body={"issue_url": ISSUE_URL, "model": "muse-spark-1.3", "effort": "high"},
    )
    assert status == 200
    token = estimate["confirm_token"]

    status, _, _ = call(
        server,
        "POST",
        "/api/actions/solve",
        body={
            "issue_url": "https://github.com/o/r/issues/2",
            "model": "muse-spark-1.3",
            "effort": "high",
            "confirm_token": token,
        },
    )
    assert status == 403

    params = {
        "issue_url": ISSUE_URL,
        "model": "muse-spark-1.3",
        "effort": "high",
    }
    status, estimate, _ = call(server, "POST", "/api/estimate/solve", body=params)
    assert status == 200
    token = estimate["confirm_token"]
    status, started, _ = call(
        server, "POST", "/api/actions/solve", body={**params, "confirm_token": token}
    )
    assert status == 200
    job = wait_job(server, started["job_id"])
    assert job["status"] == "done"
    assert job["result"]["ok"] is True

    status, _, _ = call(
        server, "POST", "/api/actions/solve", body={**params, "confirm_token": token}
    )
    assert status == 403


def test_estimate_solve_reports_recommended_max_usd(panel) -> None:
    server = panel["server"]
    status, escalated, _ = call(
        server,
        "POST",
        "/api/estimate/solve",
        body={"issue_url": ISSUE_URL, "model": "muse-spark-1.3", "effort": "high"},
    )
    assert status == 200
    assert escalated["recommended_max_usd"] == 1.20
    status, default, _ = call(
        server,
        "POST",
        "/api/estimate/solve",
        body={
            "issue_url": ISSUE_URL,
            "model": "muse-spark-1.3-contributor",
            "effort": "medium",
        },
    )
    assert status == 200
    assert default["recommended_max_usd"] == 0.60


# -- busy lock ------------------------------------------------------------


def test_busy_lock_returns_409(panel) -> None:
    server = panel["server"]
    panel["gate"].clear()
    server.runners["scout"] = panel["blocking_stub"]
    try:
        status, first, _ = call(server, "POST", "/api/actions/scout", body={"limit": 5})
        assert status == 200
        status, busy, _ = call(server, "POST", "/api/actions/scout", body={"limit": 5})
        assert status == 409
        assert busy["current_job_id"] == first["job_id"]
    finally:
        panel["gate"].set()
    job = wait_job(server, first["job_id"])
    assert job["status"] == "done"


# -- run-id validation ----------------------------------------------------


def test_invalid_run_id_is_400(panel) -> None:
    server = panel["server"]
    for path in ("/api/runs/..%2F..", "/api/scout/bad!id", "/api/triage/bad!id"):
        status, doc, _ = call(server, "GET", path)
        assert status == 400, path
        assert "run id" in doc["error"]
    status, _, _ = call(server, "GET", "/api/runs/does-not-exist-1")
    assert status == 404
    status, _, _ = call(
        server, "POST", "/api/actions/recover", body={"run_id": "../evil"}
    )
    assert status == 400
    _write_scout_report(panel["root"])
    status, _, _ = call(
        server, "POST", "/api/estimate/triage", body={"scout_run_id": "no good"}
    )
    assert status == 400
    status, _, _ = call(
        server, "POST", "/api/estimate/solve", body={"issue_url": "not-a-url"}
    )
    assert status == 400


def test_run_detail_round_trip(panel) -> None:
    server = panel["server"]
    traces_dir = panel["root"] / "traces"
    trace_log.append("r1", {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append(
        "r1",
        {"type": "model_call", "stage": "triage", "cost_usd": 1.5},
        traces_dir,
    )
    status, overview, _ = call(server, "GET", "/api/overview")
    assert status == 200
    assert overview["budget_total_usd"] == 150.0
    assert overview["total_spend_usd"] == pytest.approx(1.5)
    assert overview["runs"][0]["run_id"] == "r1"
    assert overview["runs"][0]["chain_ok"] is True
    status, detail, _ = call(server, "GET", "/api/runs/r1")
    assert status == 200
    assert detail["verdict"] == "PENDING"
    assert detail["chain"]["ok"] is True
    assert detail["cost_usd"] == pytest.approx(1.5)


# -- no secrets -----------------------------------------------------------


def test_no_secrets_in_json_responses(panel, monkeypatch) -> None:
    sentinel = "SECRET-SENTINEL-9f8e7d6c5b"
    monkeypatch.setenv("MODEL_API_KEY", sentinel)
    monkeypatch.setenv("GITHUB_TOKEN", sentinel)
    monkeypatch.setenv("SPARK_OPEN_API_KEY", sentinel)
    _write_scout_report(panel["root"])
    server = panel["server"]
    bodies: list[bytes] = []
    for method, path, payload in [
        ("GET", "/api/overview", None),
        ("GET", "/api/runs", None),
        ("GET", "/api/runs/nope-1", None),
        ("GET", "/api/scout", None),
        ("GET", "/api/scout/s1", None),
        ("GET", "/api/triage", None),
        ("GET", "/api/triage/nope-1", None),
        ("GET", "/api/jobs/nope", None),
        ("GET", "/no-such-path", None),
        ("POST", "/api/estimate/solve", {"issue_url": "not-a-url"}),
        ("POST", "/api/actions/solve", {"issue_url": ISSUE_URL}),
        ("POST", "/api/actions/recover", {"run_id": "bad id"}),
    ]:
        status, _, data = call(server, method, path, body=payload)
        assert status in (200, 400, 403, 404), (method, path, status)
        bodies.append(data)
    status, _, data = call(server, "POST", "/api/estimate/triage", raw=b"{not json")
    assert status == 400
    bodies.append(data)
    for data in bodies:
        assert sentinel.encode() not in data
        assert b"Bearer" not in data


# -- selection rule -------------------------------------------------------


def test_recommended_max_usd_rule() -> None:
    assert select_mod.recommended_max_usd("muse-spark-1.3", "high") == 1.20
    assert select_mod.recommended_max_usd("muse-spark-1.3-contributor", "medium") == (
        select_mod.DEFAULT_MAX_USD
    )
    assert select_mod.recommended_max_usd("muse-spark-1.3", "medium") == 0.60
    assert select_mod.recommended_max_usd("muse-spark-1.3-contributor", "high") == 0.60
    assert select_mod.ESCALATED_MAX_USD == 1.20
    assert select_mod.DEFAULT_MAX_USD == 0.60


# -- static panel ---------------------------------------------------------


def test_static_panel_served(panel) -> None:
    status, _, data = call(panel["server"], "GET", "/")
    assert status == 200
    assert b"panel" in data

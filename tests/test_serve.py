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
    assert escalated["recommended_max_usd"] == 2.00
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
        status, first, _ = call(
            server, "POST", "/api/actions/scout", body={"limit": 5, "guided": False}
        )
        assert status == 200
        status, busy, _ = call(
            server, "POST", "/api/actions/scout", body={"limit": 5, "guided": False}
        )
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
    assert select_mod.recommended_max_usd("muse-spark-1.3", "high") == 2.00
    assert select_mod.recommended_max_usd("muse-spark-1.3-contributor", "medium") == (
        select_mod.DEFAULT_MAX_USD
    )
    assert select_mod.recommended_max_usd("muse-spark-1.3", "medium") == 0.60
    assert select_mod.recommended_max_usd("muse-spark-1.3-contributor", "high") == 0.60
    assert select_mod.ESCALATED_MAX_USD == 2.00
    assert select_mod.DEFAULT_MAX_USD == 0.60


# -- Linux verdict + run kinds ------------------------------------------------


LINUX_DOC = {
    "verdict": "VERIFIED",
    "f2p_pass": True,
    "regressions": 0,
    "suite_scope": "test/unit",
    "ci_run": "https://github.com/o/r/actions/runs/1",
    "platform": "Linux",
    "date": "2026-11-03",
    "reason": "fail-to-pass with no regressions",
}


def _write_proof(root: Path, run_id: str, verdict_doc=None, linux_doc=None) -> None:
    proof_dir = root / "proof" / run_id
    proof_dir.mkdir(parents=True, exist_ok=True)
    if verdict_doc is not None:
        (proof_dir / "verdict.json").write_text(
            json.dumps(verdict_doc), encoding="utf-8"
        )
    if linux_doc is not None:
        (proof_dir / "linux-verify.json").write_text(
            json.dumps(linux_doc), encoding="utf-8"
        )


def _overview_entry(server, run_id: str) -> dict:
    status, overview, _ = call(server, "GET", "/api/overview")
    assert status == 200
    matches = [r for r in overview["runs"] if r["run_id"] == run_id]
    assert len(matches) == 1
    return matches[0]


def test_linux_verdict_wins_over_local(panel) -> None:
    server, root = panel["server"], panel["root"]
    run_id = "r-linux-1"
    traces_dir = root / "traces"
    trace_log.append(run_id, {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append(
        run_id, {"type": "error", "stage": "fixer", "reason": "boom"}, traces_dir
    )
    _write_proof(
        root, run_id, {"verdict": "ERROR", "reason": "boom"}, dict(LINUX_DOC)
    )
    entry = _overview_entry(server, run_id)
    assert entry["verdict"] == "VERIFIED"
    assert entry["local_verdict"] == "ERROR"
    assert entry["kind"] == "solve"
    assert entry["linux"]["verdict"] == "VERIFIED"
    assert entry["linux"]["f2p_pass"] is True
    assert entry["linux"]["regressions"] == 0
    assert entry["linux"]["suite_scope"] == "test/unit"
    assert entry["linux"]["ci_run"] == LINUX_DOC["ci_run"]
    assert entry["linux"]["platform"] == "Linux"
    assert entry["linux"]["date"] == "2026-11-03"
    status, detail, _ = call(server, "GET", f"/api/runs/{run_id}")
    assert status == 200
    assert detail["verdict"] == "VERIFIED"
    assert detail["local_verdict"] == "ERROR"
    assert detail["kind"] == "solve"
    assert detail["linux"]["verdict"] == "VERIFIED"


def test_no_linux_file_keeps_local_verdict(panel) -> None:
    server, root = panel["server"], panel["root"]
    run_id = "r-local-1"
    trace_log.append(
        run_id, {"type": "run_started", "stage": "run"}, root / "traces"
    )
    entry = _overview_entry(server, run_id)
    assert entry["verdict"] == "PENDING"
    assert entry["local_verdict"] == "PENDING"
    assert entry["linux"] is None
    assert entry["kind"] == "solve"
    status, detail, _ = call(server, "GET", f"/api/runs/{run_id}")
    assert status == 200
    assert detail["verdict"] == "PENDING"
    assert detail["local_verdict"] == "PENDING"
    assert detail["linux"] is None


def test_scout_triage_runs_get_kind_labels(panel) -> None:
    server, root = panel["server"], panel["root"]
    traces_dir = root / "traces"
    for run_id in ("scout-aaa1", "triage-aaa1"):
        trace_log.append(
            run_id, {"type": "run_started", "stage": "run"}, traces_dir
        )
    scout = _overview_entry(server, "scout-aaa1")
    assert scout["verdict"] == "SCOUT"
    assert scout["local_verdict"] == "PENDING"
    assert scout["kind"] == "scout"
    assert scout["linux"] is None
    triage = _overview_entry(server, "triage-aaa1")
    assert triage["verdict"] == "TRIAGE"
    assert triage["local_verdict"] == "PENDING"
    assert triage["kind"] == "triage"
    assert triage["linux"] is None
    status, detail, _ = call(server, "GET", "/api/runs/scout-aaa1")
    assert status == 200
    assert detail["verdict"] == "SCOUT"
    assert detail["kind"] == "scout"


# -- static panel ---------------------------------------------------------


def test_static_panel_served(panel) -> None:
    status, _, data = call(panel["server"], "GET", "/")
    assert status == 200
    assert b"panel" in data


def test_logo_served_as_svg(panel) -> None:
    (panel["root"] / "web" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8"
    )
    handler = serve_mod.PanelHandler.__new__(serve_mod.PanelHandler)
    msg = Message()
    msg["Host"] = LOCAL_HOST
    handler.server = panel["server"]
    handler.command = "GET"
    handler.path = "/logo.svg"
    handler.requestline = "GET /logo.svg HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.headers = msg
    handler.rfile = io.BytesIO(b"")
    handler.wfile = io.BytesIO()
    handler.do_GET()
    head, _, data = handler.wfile.getvalue().partition(b"\r\n\r\n")
    assert b" 200 " in head.split(b"\r\n")[0]
    assert b"image/svg+xml" in head
    assert b"<svg" in data


def test_unknown_path_is_404(panel) -> None:
    status, doc, _ = call(panel["server"], "GET", "/no-such-asset.xyz")
    assert status == 404
    assert doc["error"] == "not found"


def test_app_js_has_no_inner_html() -> None:
    app_js = Path(__file__).resolve().parents[1] / "web" / "app.js"
    source = app_js.read_text(encoding="utf-8")
    assert "innerHTML" not in source


def test_video_runs_get_kind_label(panel) -> None:
    server, root = panel["server"], panel["root"]
    traces_dir = root / "traces"
    trace_log.append(
        "video-aaa1", {"type": "run_started", "stage": "run"}, traces_dir
    )
    entry = _overview_entry(server, "video-aaa1")
    assert entry["verdict"] == "VIDEO"
    assert entry["local_verdict"] == "PENDING"
    assert entry["kind"] == "video"
    assert entry["linux"] is None
    status, detail, _ = call(server, "GET", "/api/runs/video-aaa1")
    assert status == 200
    assert detail["verdict"] == "VIDEO"
    assert detail["kind"] == "video"


# -- Prompt 24: best evidence, reconciled cost, fixes view ------------------


def _write_reconciliation(root: Path, payload) -> None:
    recon_dir = root / "evals" / "results"
    recon_dir.mkdir(parents=True, exist_ok=True)
    (recon_dir / "cost-reconciliation.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )


def _write_error_md(root: Path, run_id: str, text: str) -> None:
    proof_dir = root / "proof" / run_id
    proof_dir.mkdir(parents=True, exist_ok=True)
    (proof_dir / "error.md").write_text(text, encoding="utf-8")


def test_best_f2p_linux_overrides_local_error_in_detail(panel) -> None:
    server, root = panel["server"], panel["root"]
    run_id = "r-best-1"
    traces_dir = root / "traces"
    trace_log.append(run_id, {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append(
        run_id,
        {"type": "error", "stage": "fixer", "reason": "solver failed: bad json"},
        traces_dir,
    )
    trace_log.append(
        run_id,
        {
            "type": "verify_verdict",
            "stage": "verifier",
            "verdict": "ERROR",
            "reason": "pytest exited with code 4 on base",
            "f2p_pass": False,
            "regressions": 0,
        },
        traces_dir,
    )
    _write_proof(
        root,
        run_id,
        {
            "verdict": "ERROR",
            "reason": "pytest exited with code 4 on base",
            "issue_url": ISSUE_URL,
            "test_file": "test/unit/test_x.py",
            "f2p_pass": False,
            "regressions": 0,
            "fix_commit": "abc123",
        },
        dict(LINUX_DOC),
    )
    _write_error_md(
        root, run_id, "# Solver error — o/r#1\n\nThe solver session failed.\n"
    )
    status, detail, _ = call(server, "GET", f"/api/runs/{run_id}")
    assert status == 200
    assert detail["verdict"] == "VERIFIED"
    assert detail["local_verdict"] == "ERROR"
    f2p = detail["f2p"]
    assert f2p["source"] == "linux"
    assert f2p["f2p_pass"] is True
    assert f2p["regressions"] == 0
    assert f2p["suite_scope"] == "test/unit"
    assert f2p["ci_run"] == LINUX_DOC["ci_run"]
    assert f2p["test_path"] == "test/unit/test_x.py"
    assert detail["local_summary"].startswith("Local run (Windows): ERROR — ")
    assert "Solver error" in detail["local_summary"]
    assert detail["issue_url"] == ISSUE_URL


def test_best_f2p_local_when_linux_does_not_pass(panel) -> None:
    server, root = panel["server"], panel["root"]
    run_id = "r-best-2"
    traces_dir = root / "traces"
    trace_log.append(run_id, {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append(
        run_id,
        {
            "type": "verify_verdict",
            "stage": "verifier",
            "verdict": "ERROR",
            "reason": "no failure on base",
            "f2p_pass": False,
            "regressions": 0,
        },
        traces_dir,
    )
    status, detail, _ = call(server, "GET", f"/api/runs/{run_id}")
    assert status == 200
    f2p = detail["f2p"]
    assert f2p["source"] == "local"
    assert f2p["f2p_pass"] is False
    assert detail["local_summary"].startswith("Local run (Windows): ERROR")


def test_work_fields_fall_back_to_proof_verdict(panel) -> None:
    server, root = panel["server"], panel["root"]
    run_id = "r-best-3"
    traces_dir = root / "traces"
    trace_log.append(run_id, {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append(
        run_id,
        {"type": "model_call", "stage": "fixer", "cost_usd": 0.5},
        traces_dir,
    )
    _write_proof(
        root,
        run_id,
        {
            "verdict": "VERIFIED",
            "reason": "fail-to-pass with no regressions",
            "test_file": "tests/test_zip_slip.py",
            "f2p_pass": True,
            "regressions": 0,
            "fix_commit": "b8f16acd",
            "effort": "medium",
            "patch_summary": "Confined extraction to the target dir.",
        },
        None,
    )
    status, detail, _ = call(server, "GET", f"/api/runs/{run_id}")
    assert status == 200
    work = detail["work"]
    assert work["repro_result"]["test_path"] == "tests/test_zip_slip.py"
    assert work["verify_verdict"]["f2p_pass"] is True
    assert work["verify_verdict"]["regressions"] == 0
    assert work["fix_attempt"]["patch_hash"] == "b8f16acd"
    assert detail["f2p"]["source"] == "local"
    assert detail["f2p"]["f2p_pass"] is True


def test_reconciliation_corrects_displayed_cost(panel) -> None:
    server, root = panel["server"], panel["root"]
    run_id = "r-cost-1"
    traces_dir = root / "traces"
    trace_log.append(run_id, {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append(
        run_id,
        {"type": "model_call", "stage": "fixer", "cost_usd": 1.059},
        traces_dir,
    )
    _write_reconciliation(
        root,
        {
            "runs": {
                run_id: {
                    "logged_usd": 1.059,
                    "cost_usd": 1.23,
                    "basis": "reasoning tokens billed at the output rate",
                }
            }
        },
    )
    entry = _overview_entry(server, run_id)
    assert entry["cost_usd"] == pytest.approx(1.23)
    assert entry["cost_logged_usd"] == pytest.approx(1.059)
    assert entry["cost_basis"] == "reasoning tokens billed at the output rate"
    assert entry["cost_corrected"] is True
    status, overview, _ = call(server, "GET", "/api/overview")
    assert status == 200
    assert overview["total_spend_usd"] == pytest.approx(1.059)
    status, detail, _ = call(server, "GET", f"/api/runs/{run_id}")
    assert status == 200
    assert detail["cost_usd"] == pytest.approx(1.23)
    assert detail["cost_logged_usd"] == pytest.approx(1.059)
    assert detail["cost_corrected"] is True


def test_reconciliation_malformed_ignored(panel) -> None:
    server, root = panel["server"], panel["root"]
    run_id = "r-cost-2"
    traces_dir = root / "traces"
    trace_log.append(run_id, {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append(
        run_id,
        {"type": "model_call", "stage": "fixer", "cost_usd": 0.25},
        traces_dir,
    )
    for bad in ("{not json", '{"runs": [1, 2]}', '{"runs": {"x": {}}}'):
        _write_reconciliation(root, bad)
        status, overview, _ = call(server, "GET", "/api/overview")
        assert status == 200
        entry = _overview_entry(server, run_id)
        assert entry["cost_usd"] == pytest.approx(0.25)
        assert entry["cost_corrected"] is False
        assert entry["cost_logged_usd"] is None
        assert overview["total_spend_usd"] == pytest.approx(0.25)


def test_fixes_view_contract_excludes_video_runs(panel) -> None:
    server, root = panel["server"], panel["root"]
    traces_dir = root / "traces"
    trace_log.append(
        "video-zzz1", {"type": "run_started", "stage": "run"}, traces_dir
    )
    trace_log.append(
        "20261102-zzz1-o-r-1", {"type": "run_started", "stage": "run"}, traces_dir
    )
    status, overview, _ = call(server, "GET", "/api/overview")
    assert status == 200
    by_id = {r["run_id"]: r for r in overview["runs"]}
    assert by_id["video-zzz1"]["kind"] == "video"
    assert by_id["20261102-zzz1-o-r-1"]["kind"] == "solve"
    fixes = [r for r in overview["runs"] if r["kind"] == "solve"]
    assert "video-zzz1" not in {r["run_id"] for r in fixes}
    assert "20261102-zzz1-o-r-1" in {r["run_id"] for r in fixes}
    for run in overview["runs"]:
        assert "verdict" in run and "run_id" in run


# -- Prompt 27: guided discovery by default in run and scout --------------


def test_guided_scout_refused_without_confirm_token(panel) -> None:
    server = panel["server"]
    status, doc, _ = call(server, "POST", "/api/actions/scout", body={"limit": 5})
    assert status == 403
    assert "confirm token" in doc["error"]
    status, doc, _ = call(
        server,
        "POST",
        "/api/actions/scout",
        body={"limit": 5, "guided": True, "confirm_token": "bogus"},
    )
    assert status == 403
    assert "confirm token" in doc["error"]


def test_unguided_scout_stays_free(panel) -> None:
    server = panel["server"]
    status, started, _ = call(
        server, "POST", "/api/actions/scout", body={"limit": 5, "guided": False}
    )
    assert status == 200
    job = wait_job(server, started["job_id"])
    assert job["status"] == "done"
    assert panel["calls"][-1][1]["guided"] is False


def test_estimate_scout_guided_token_flow(panel) -> None:
    server = panel["server"]
    status, estimate, _ = call(
        server, "POST", "/api/estimate/scout", body={"limit": 5}
    )
    assert status == 200
    assert estimate["guided"] is True
    assert estimate["est_calls"] == 1
    assert estimate["confirm_token"]
    token = estimate["confirm_token"]
    status, started, _ = call(
        server,
        "POST",
        "/api/actions/scout",
        body={
            "limit": 5,
            "languages": "python,node",
            "guided": True,
            "confirm_token": token,
        },
    )
    assert status == 200
    job = wait_job(server, started["job_id"])
    assert job["status"] == "done"
    assert panel["calls"][-1][1]["guided"] is True


def test_estimate_scout_unguided_has_no_token(panel) -> None:
    server = panel["server"]
    status, estimate, _ = call(
        server, "POST", "/api/estimate/scout", body={"limit": 5, "guided": False}
    )
    assert status == 200
    assert estimate["guided"] is False
    assert estimate["est_calls"] == 0
    assert "confirm_token" not in estimate


def test_run_token_carries_guided(panel) -> None:
    server = panel["server"]
    params = {"languages": "python", "limit": 5, "max_usd": 0.5}
    status, estimate, _ = call(server, "POST", "/api/estimate/run", body=params)
    assert status == 200
    assert estimate["guided"] is True
    assert estimate["est_calls"] == 4  # brief + triage + critic + guided query proposal
    assert estimate["brief_est_usd"] is not None
    token = estimate["confirm_token"]
    status, started, _ = call(
        server,
        "POST",
        "/api/actions/run",
        body={**params, "guided": True, "confirm_token": token},
    )
    assert status == 200
    job = wait_job(server, started["job_id"])
    assert job["status"] == "done"
    assert panel["calls"][-1][1]["guided"] is True


def test_run_token_rejects_guided_mismatch(panel) -> None:
    server = panel["server"]
    params = {"languages": "python", "limit": 5, "max_usd": 0.5}
    status, estimate, _ = call(server, "POST", "/api/estimate/run", body=params)
    assert status == 200
    status, doc, _ = call(
        server,
        "POST",
        "/api/actions/run",
        body={**params, "guided": False, "confirm_token": estimate["confirm_token"]},
    )
    assert status == 403
    assert "confirm token" in doc["error"]


def test_scout_detail_guided_and_legacy(panel) -> None:
    server, root = panel["server"], panel["root"]
    _write_scout_report(root, "s1")
    (root / "scout" / "g1.json").write_text(
        json.dumps(
            {
                "run_id": "g1",
                "guided": True,
                "proposed_queries": [
                    {
                        "keywords": "crash empty input",
                        "class": None,
                        "why": "matches the traceback",
                    }
                ],
                "rejected_queries": [
                    {
                        "keywords": "bug",
                        "class": None,
                        "why": "",
                        "reason": "too generic",
                    }
                ],
                "guided_rationale": "Prefer repro terms.",
                "rate_limited": False,
                "candidates": [
                    {
                        "issue_url": ISSUE_URL,
                        "repo": "o/r",
                        "number": 1,
                        "title": "Crash on empty input",
                        "matched_query": "crash empty input",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    status, legacy, _ = call(server, "GET", "/api/scout/s1")
    assert status == 200
    assert "proposed_queries" not in legacy
    assert legacy["candidates"][0].get("matched_query") is None
    status, guided, _ = call(server, "GET", "/api/scout/g1")
    assert status == 200
    assert guided["guided"] is True
    assert guided["proposed_queries"][0]["why"] == "matches the traceback"
    assert guided["rejected_queries"][0]["reason"] == "too generic"
    assert guided["candidates"][0]["matched_query"] == "crash empty input"


# -- Prompt 29: confirm tokens bind normalized params -------------------------


def _run_token(server, body):
    """A fresh confirm token for ``body`` from ``/api/estimate/run``."""
    status, estimate, _ = call(server, "POST", "/api/estimate/run", body=body)
    assert status == 200
    return estimate["confirm_token"]


def test_run_estimate_confirms_integer_max_usd(panel) -> None:
    """An integer Max USD binds the same token as the normalized float."""
    server = panel["server"]
    body = {"languages": "python", "limit": 5, "max_usd": 3}
    token = _run_token(server, body)
    status, started, _ = call(
        server,
        "POST",
        "/api/actions/run",
        body={**body, "confirm_token": token},
    )
    assert status == 200
    job = wait_job(server, started["job_id"])
    assert job["status"] == "done"
    assert job["result"]["params"]["max_usd"] == 3.0


def test_run_estimate_confirms_float_and_string_limit(panel) -> None:
    """A float budget and a string limit confirm against normalized params."""
    server = panel["server"]
    body = {"languages": "python", "limit": "5", "max_usd": 2.5}
    token = _run_token(server, body)
    status, started, _ = call(
        server,
        "POST",
        "/api/actions/run",
        body={**body, "confirm_token": token},
    )
    assert status == 200
    job = wait_job(server, started["job_id"])
    assert job["status"] == "done"
    assert job["result"]["params"]["limit"] == 5
    assert job["result"]["params"]["max_usd"] == 2.5


def test_run_changed_value_is_refused(panel) -> None:
    """A changed budget or limit no longer matches the token."""
    server = panel["server"]
    body = {"languages": "python", "limit": 5, "max_usd": 3}
    for bad in ({"max_usd": 3.5}, {"limit": 6}):
        token = _run_token(server, body)
        status, doc, _ = call(
            server,
            "POST",
            "/api/actions/run",
            body={**body, **bad, "confirm_token": token},
        )
        assert status == 403, bad
        assert "confirm token" in doc["error"]


def test_solve_confirms_integer_max_usd(panel) -> None:
    """An integer solve budget confirms; a changed one is refused."""
    server = panel["server"]
    body = {
        "issue_url": ISSUE_URL,
        "model": "muse-spark-1.3",
        "effort": "high",
        "max_usd": 3,
    }
    status, estimate, _ = call(server, "POST", "/api/estimate/solve", body=body)
    assert status == 200
    status, started, _ = call(
        server,
        "POST",
        "/api/actions/solve",
        body={**body, "confirm_token": estimate["confirm_token"]},
    )
    assert status == 200
    job = wait_job(server, started["job_id"])
    assert job["status"] == "done"

    status, fresh, _ = call(server, "POST", "/api/estimate/solve", body=body)
    assert status == 200
    status, doc, _ = call(
        server,
        "POST",
        "/api/actions/solve",
        body={**body, "max_usd": 3.5, "confirm_token": fresh["confirm_token"]},
    )
    assert status == 403
    assert "confirm token" in doc["error"]


# -- Prompt 29: briefs files are not scout reports ----------------------------


def test_scout_list_excludes_briefs_files(panel) -> None:
    """``*.briefs.json`` must not appear in ``GET /api/scout``."""
    server, root = panel["server"], panel["root"]
    _write_scout_report(root, "s1")
    (root / "scout" / "s1.briefs.json").write_text(
        json.dumps({"scout_run_id": "s1", "briefs": {}}), encoding="utf-8"
    )
    status, doc, _ = call(server, "GET", "/api/scout")
    assert status == 200
    run_ids = [report["run_id"] for report in doc["reports"]]
    assert "s1" in run_ids
    assert "s1.briefs" not in run_ids
    assert not any(run_id.endswith(".briefs") for run_id in run_ids)

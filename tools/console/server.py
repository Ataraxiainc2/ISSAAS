"""Muse console: a local read-only web view of `muse exec --json` runs in muse-runs/.

Run:  python tools/console/server.py   then open http://127.0.0.1:8765
Standard library only; binds to localhost; never calls any model or network service.
"""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = ROOT / "muse-runs"
PORT = 8765

# USD per 1M tokens (Meta Model API pricing page).
PRICES = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
}


def cost(model, inp, cached, out, reasoning=0):
    p = PRICES.get(model)
    if not p:
        return None
    # Reasoning tokens are disjoint from output tokens and billed at the
    # output rate (Meta Model API); old records without the field count 0.
    return ((inp - cached) * p[0] + cached * p[1] + (out + reasoning) * p[2]) / 1e6


def list_runs():
    runs = []
    for log in sorted(RUNS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = load_meta(log.stem)
        runs.append(
            {
                "run": log.stem,
                "finished": bool(meta.get("finished")) or meta.get("legacy", False),
                "started": meta.get("started"),
            }
        )
    return runs


def load_meta(stem):
    path = RUNS / f"{stem}.meta.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    # Runs started before the run script existed: derive the prompt from the name.
    prompt = ROOT / "prompts" / (re.sub(r"-\d{8}-\d{6}$", "", stem) + ".md")
    return {
        "run": stem,
        "prompt_file": str(prompt.relative_to(ROOT)) if prompt.exists() else None,
        "legacy": True,
        "note": "Started before the run script; settings were not recorded.",
    }


def parse_run(stem):
    meta = load_meta(stem)
    prompt_text = None
    if meta.get("prompt_file") and (ROOT / meta["prompt_file"]).exists():
        prompt_text = (ROOT / meta["prompt_file"]).read_text(encoding="utf-8")

    items, tools, usage, done, model_calls = [], {}, None, False, 0
    log = RUNS / f"{stem}.jsonl"
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue  # partially written line while the run is live
        kind, p = e.get("payload_type"), e.get("payload", {})
        ev = p.get("event", {})
        if kind == "run.output.delta":
            if items and items[-1]["type"] == "reply":
                items[-1]["text"] += p.get("text", "")
            else:
                items.append({"type": "reply", "text": p.get("text", "")})
        elif kind == "task.lifecycle.proposed":
            tk = ev.get("task_kind", "")
            if tk == "model.meta.response":
                model_calls += 1
                items.append({"type": "model_call", "n": model_calls})
            elif tk.startswith("tool."):
                item = {"type": "tool", "name": tk[5:], "output": "", "status": "running"}
                tools[ev["task_id"]] = item
                items.append(item)
        elif kind == "task.lifecycle.output" and ev.get("task_id") in tools:
            tools[ev["task_id"]]["output"] += ev.get("chunk", "")
        elif kind == "task.lifecycle.completed" and ev.get("task_id") in tools:
            tools[ev["task_id"]]["status"] = "ok"
        elif kind == "task.lifecycle.failed" and ev.get("task_id") in tools:
            tools[ev["task_id"]]["status"] = "failed: " + ev.get("reason", "")
        elif kind == "run.usage":
            usage = p.get("models", [])
            for m in usage:
                m["cost_usd"] = cost(
                    m["model"],
                    m["input_tokens"],
                    m.get("cached_tokens", 0),
                    m["output_tokens"],
                    m.get("reasoning_tokens", 0) or 0,
                )
        elif kind == "run.terminal.completed":
            done = True

    for t in tools.values():
        if len(t["output"]) > 6000:
            t["output"] = t["output"][:6000] + "\n… (truncated)"
    err = RUNS / f"{stem}.err.txt"
    return {
        "meta": meta,
        "prompt": prompt_text,
        "items": items,
        "usage": usage,
        "done": done or bool(meta.get("finished")),
        "model_calls": model_calls,
        "stderr": err.read_text(encoding="utf-8", errors="replace")[-3000:] if err.exists() else "",
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/runs":
            return self.json(
                {
                    "runs": list_runs(),
                    "config": json.loads((HERE / "config.json").read_text(encoding="utf-8")),
                }
            )
        m = re.fullmatch(r"/api/run/([\w.-]+)", self.path)
        if m and (RUNS / f"{m.group(1)}.jsonl").exists():
            return self.json(parse_run(m.group(1)))
        self.send(404, b"not found", "text/plain")

    def json(self, obj):
        self.send(200, json.dumps(obj).encode(), "application/json")

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Muse console on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

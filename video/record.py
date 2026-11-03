"""Record the live screen clips for demo video v2 (administrator tool, not part of the product).

Drives a headless Microsoft Edge through the Chrome DevTools Protocol and records what it renders with
`Page.startScreencast`, so nothing opens on the desktop. A visible cursor shows every click. Terminal clips run the real
command now and stream its output into a terminal-styled page as it is printed.

    uv run --no-sync python video/record.py all          # every clip into video/rec/
    uv run --no-sync python video/record.py e-attacks    # one clip

Needs the panel (`issa serve --port 8782`) and the Muse console (`python tools/console/server.py`, port 8765)
running. Clip b-triage makes one paid Muse Spark Open triage call on a fresh, small scout run.
Standard library only (a minimal WebSocket client is included).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "video" / "rec"
WORK = ROOT / ".cache" / "record"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PANEL = "http://127.0.0.1:8782"
CONSOLE = "http://127.0.0.1:8765"
TERM_PORT = 8791
SILENT_UNTIL_DONE = {"eval"}  # terminal commands that print only at the end
CDP_PORT = 9333
W, H, SCALE = 1600, 900, 1.2  # terminal and GitHub: CSS viewport, frames come out 1920x1080
PW, PH, PSCALE = 1280, 720, 1.5  # panel and Muse console: larger text
UV = str(ROOT / ".tools" / "uv.exe")
CI_RUN = "https://github.com/Ataraxiainc2/ISSAAS/actions/runs/41613094986"


# ---------------------------------------------------------------- minimal WebSocket client (RFC 6455)
class WebSocket:
    def __init__(self, url: str) -> None:
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=120)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        head, rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError("websocket upgrade refused")
        self.buf = bytearray(rest)
        self.send_lock = threading.Lock()

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.send_lock:
            self.sock.sendall(bytes(head) + mask + body)

    def recv(self) -> str:
        message = b""
        while True:
            b1, b2 = self._exact(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            if b2 & 0x80:
                self._exact(4)
            payload = self._exact(n)
            op = b1 & 0x0F
            if op == 8:
                raise EOFError
            if op in (9, 10):
                continue
            message += payload
            if b1 & 0x80:
                return message.decode("utf-8", "replace")


class CDP:
    def __init__(self, ws_url: str) -> None:
        self.ws = WebSocket(ws_url)
        self.next_id = 0
        self.lock = threading.Lock()
        self.cv = threading.Condition()
        self.results: dict[int, dict] = {}
        self.handlers: dict[str, object] = {}
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                return
            if "id" in msg:
                with self.cv:
                    self.results[msg["id"]] = msg
                    self.cv.notify_all()
            elif msg.get("method") in self.handlers:
                self.handlers[msg["method"]](msg.get("params", {}))

    def send(self, method: str, **params) -> int:
        with self.lock:
            self.next_id += 1
            i = self.next_id
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        return i

    def call(self, method: str, **params) -> dict:
        i = self.send(method, **params)
        with self.cv:
            if not self.cv.wait_for(lambda: i in self.results, timeout=120):
                raise TimeoutError(method)
            msg = self.results.pop(i)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})


# ---------------------------------------------------------------- screencast recorder
class Recorder:
    """Keeps every screencast frame with its time; paused stretches are cut out of the timeline."""

    def __init__(self, cdp: CDP) -> None:
        self.cdp = cdp
        self.frames: list[tuple[float, bytes]] = []
        self.on = False
        self.offset = 0.0
        self.paused_at: float | None = None
        cdp.handlers["Page.screencastFrame"] = self._frame

    def _frame(self, p: dict) -> None:
        self.cdp.send("Page.screencastFrameAck", sessionId=p["sessionId"])
        if self.on and self.paused_at is None:
            self.frames.append((time.monotonic() - self.offset, base64.b64decode(p["data"])))

    def start(self) -> None:
        self.frames, self.offset, self.paused_at, self.on = [], 0.0, None, True
        self.cdp.call(
            "Page.startScreencast",
            format="jpeg",
            quality=88,
            maxWidth=1920,
            maxHeight=1080,
            everyNthFrame=1,
        )
        self.t0 = time.monotonic()

    def pause(self) -> None:
        self.paused_at = time.monotonic()

    def resume(self) -> None:
        if self.paused_at is not None:
            self.offset += time.monotonic() - self.paused_at - 0.5  # keep a 0.5 s beat at the cut
            self.paused_at = None

    def stop(self, name: str) -> Path:
        time.sleep(0.6)
        self.on = False
        self.cdp.call("Page.stopScreencast")
        end = time.monotonic() - self.offset
        frames_dir = WORK / name
        shutil.rmtree(frames_dir, ignore_errors=True)
        frames_dir.mkdir(parents=True)
        lines = []
        for i, (t, jpeg) in enumerate(self.frames):
            nxt = self.frames[i + 1][0] if i + 1 < len(self.frames) else end
            (frames_dir / f"{i:05d}.jpg").write_bytes(jpeg)
            lines.append(f"file '{i:05d}.jpg'\nduration {max(0.034, nxt - t):.3f}\n")
        if self.frames:
            lines.append(f"file '{len(self.frames) - 1:05d}.jpg'\n")
        (frames_dir / "list.txt").write_text("".join(lines), encoding="utf-8")
        REC.mkdir(parents=True, exist_ok=True)
        out = REC / f"{name}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                "list.txt",
                "-vf",
                "scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                str(out),
            ],
            check=True,
            cwd=frames_dir,
            stdin=subprocess.DEVNULL,
        )
        print(f"{name}: {len(self.frames)} frames, {end - self.t0:.1f}s -> {out.relative_to(ROOT)}")
        return out


# ---------------------------------------------------------------- page helpers (visible cursor)
DEMO_JS = r"""
(() => {
  if (window.__demo) return;
  const css = document.createElement('style');
  css.textContent = `#__cur{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
    transition:transform .65s cubic-bezier(.3,.7,.3,1);filter:drop-shadow(0 2px 3px rgba(0,0,0,.6))}
    .__ring{position:fixed;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;border:3px solid #4c8dff;
    z-index:2147483646;pointer-events:none;animation:__r .5s ease-out forwards}
    @keyframes __r{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}
    .__hl{outline:3px solid #4c8dff !important;outline-offset:2px;transition:outline .2s}`;
  const cur = document.createElement('div');
  cur.id = '__cur';
  cur.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M3 2l7 19 2.6-7.4L20 11z" fill="#fff" stroke="#111" stroke-width="1.4"/></svg>';
  const add = () => { document.head.appendChild(css); document.body.appendChild(cur); };
  if (document.body) add(); else document.addEventListener('DOMContentLoaded', add);
  let x = 800, y = 450;
  cur.style.transform = `translate(${x}px,${y}px)`;
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const find = (sel, text) => {
    const all = [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null || e.tagName === 'OPTION');
    if (!text) return all[0];  // with text: the innermost element that contains it
    return all.filter(e => e.textContent.includes(text)).sort((p, q) => p.textContent.length - q.textContent.length)[0];
  };
  window.__demo = {
    async move(el) {
      el.scrollIntoView({block: 'center', behavior: 'smooth'});
      await sleep(500);
      const r = el.getBoundingClientRect();
      x = r.left + Math.min(r.width / 2, 60); y = r.top + r.height / 2;
      cur.style.transform = `translate(${x}px,${y}px)`;
      await sleep(750);
    },
    async click(sel, text) {
      const el = find(sel, text);
      if (!el) throw new Error('not found: ' + sel + ' ' + (text || ''));
      await this.move(el);
      const ring = document.createElement('div'); ring.className = '__ring';
      ring.style.left = x + 'px'; ring.style.top = y + 'px'; document.body.appendChild(ring);
      setTimeout(() => ring.remove(), 600);
      el.click();
      await sleep(400);
      return true;
    },
    async type(sel, value) {
      const el = find(sel);
      await this.move(el);
      el.focus(); el.value = '';
      for (const ch of String(value)) {
        el.value += ch; el.dispatchEvent(new Event('input', {bubbles: true})); await sleep(55);
      }
      el.dispatchEvent(new Event('change', {bubbles: true}));
      await sleep(300);
      return true;
    },
    async hover(sel, text) { const el = find(sel, text); if (el) { el.classList.add('__hl'); await this.move(el);
      await sleep(350); el.classList.remove('__hl'); } return true; },
    async scrollToText(text, ms) {
      const el = [...document.querySelectorAll('h1,h2,h3,h4')].find(e => e.textContent.includes(text));
      if (!el) return false;
      const dy = el.getBoundingClientRect().top - 70;
      return this.scroll(dy, ms);
    },
    async scroll(dy, ms) {
      const target = document.scrollingElement;
      const from = target.scrollTop, t0 = performance.now();
      await new Promise(done => { const step = (now) => { const k = Math.min(1, (now - t0) / ms);
        target.scrollTop = from + dy * (k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
        k < 1 ? requestAnimationFrame(step) : done(); }; requestAnimationFrame(step); });
      return true;
    },
  };
})();
"""


class Page:
    def __init__(self, cdp: CDP) -> None:
        self.cdp = cdp
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", source=DEMO_JS)
        self.viewport(W, H, SCALE)

    def viewport(self, w: int, h: int, scale: float) -> None:
        self.cdp.call(
            "Emulation.setDeviceMetricsOverride",
            width=w,
            height=h,
            deviceScaleFactor=scale,
            mobile=False,
        )

    def js(self, expr: str):
        r = self.cdp.call(
            "Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True
        )
        if r.get("exceptionDetails"):
            raise RuntimeError(
                f"js: {r['exceptionDetails'].get('exception', {}).get('description', expr)}"
            )
        return r.get("result", {}).get("value")

    def goto(self, url: str, settle: float = 2.5) -> None:
        self.cdp.call("Page.navigate", url=url)
        self.wait("document.readyState === 'complete'", 60)
        self.js(DEMO_JS)
        time.sleep(settle)

    def wait(self, cond: str, timeout: float = 60, every: float = 0.5) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(f"!!({cond})"):
                    return True
            except RuntimeError:
                pass
            time.sleep(every)
        return False

    def click(self, sel: str, text: str | None = None) -> None:
        self.js(f"__demo.click({json.dumps(sel)}, {json.dumps(text)})")

    def type(self, sel: str, value: str) -> None:
        self.js(f"__demo.type({json.dumps(sel)}, {json.dumps(value)})")

    def hover(self, sel: str, text: str | None = None) -> None:
        self.js(f"__demo.hover({json.dumps(sel)}, {json.dumps(text)})")

    def scroll_to(self, text: str, ms: int = 2500) -> None:
        self.js(f"__demo.scrollToText({json.dumps(text)}, {ms})")
        time.sleep(0.3)

    def follow_job(self, seconds: float) -> bool:
        """Keep the streaming job log in view; True once the job finished."""
        end = time.time() + seconds
        while time.time() < end:
            self.js(
                "(()=>{const b=document.getElementById('job-box');const r=b.getBoundingClientRect();"
                "window.scrollBy({top:r.bottom-innerHeight+30,behavior:'smooth'});return true})()"
            )
            if self.js(JOB_DONE):
                return True
            time.sleep(1.5)
        return False

    def scroll(self, dy: int, ms: int = 1800) -> None:
        self.js(f"__demo.scroll({dy}, {ms})")
        time.sleep(0.3)


# ---------------------------------------------------------------- terminal page fed by a real process
TERM_HTML = """<!doctype html><meta charset="utf-8"><title>PowerShell</title><style>
body{margin:0;background:#0c0c0c;color:#d4d4d4;font:22px/1.42 'Cascadia Mono','Cascadia Code',Consolas,monospace}
.bar{background:#1f1f1f;color:#ccc;font:16px 'Segoe UI',sans-serif;padding:10px 18px;border-bottom:1px solid #333}
#t{padding:18px 26px;white-space:pre-wrap;word-break:break-word}.p{color:#5fb3ff}.c{color:#f2f2f2}
.ok{color:#7ee2a8}.bad{color:#ff8f8f}.cur{display:inline-block;width:12px;height:24px;background:#d4d4d4;
vertical-align:text-bottom;animation:b 1s steps(1) infinite}@keyframes b{50%{opacity:0}}#__cur{display:none}</style>
<div class="bar">Windows PowerShell — D:\\SC\\ISSAAS</div><div id="t"></div><script>
const t=document.getElementById('t');const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const paint=s=>esc(s).replace(/\\b(VERIFIED|ok|OK|rejected|passed|accepted)\\b/g,'<span class=ok>$1</span>')
  .replace(/\\b(UNPROVEN|REGRESSION|ERROR|BROKEN|FAILED)\\b/g,'<span class=bad>$1</span>');
window.runCmd=async(id,shown)=>{const line=document.createElement('div');line.innerHTML='<span class=p>PS D:\\\\SC\\\\ISSAAS&gt; </span><span class=c></span><span class=cur></span>';
 t.appendChild(line);const c=line.querySelector('.c');for(const ch of shown){c.textContent+=ch;await sleep(38);}
 await sleep(350);line.querySelector('.cur').remove();const out=document.createElement('div');t.appendChild(out);
 const r=await fetch('/run/'+id);const rd=r.body.getReader();const dec=new TextDecoder();let all='';
 while(true){const {done,value}=await rd.read();if(done)break;all+=dec.decode(value,{stream:true});
  out.innerHTML=paint(all);window.scrollTo(0,document.body.scrollHeight);}
 return true;};
</script>"""

TABLE_PS = (
    r"Get-Content evals\results\verifier-synthetic.json | ConvertFrom-Json | % attacks | "
    "Format-Table name, role, expected, got, ok, seconds -AutoSize"
)
COMMANDS = {  # id: (argv run now, the command line shown)
    "eval": ([UV, "run", "--no-sync", "issa", "eval", "verifier"], "uv run issa eval verifier"),
    "table": (
        ["powershell", "-NoProfile", "-Command", TABLE_PS + " | Out-String -Width 160"],
        TABLE_PS,
    ),
    "verify": (
        [UV, "run", "--no-sync", "issa", "verify", "triage-scout-20261102-222258"],
        "uv run issa verify triage-scout-20261102-222258",
    ),
    "replay": (
        [UV, "run", "--no-sync", "issa", "replay", "triage-scout-20261102-222258"],
        "uv run issa replay triage-scout-20261102-222258 | more",
    ),
}


class TermHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/":
            body = TERM_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        key = self.path.rsplit("/", 1)[-1]
        if key not in COMMANDS:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()  # HTTP/1.0: the body streams until the connection closes
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
        proc = subprocess.Popen(
            COMMANDS[key][0],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        lines = 0
        for raw in iter(proc.stdout.readline, b""):
            lines += 1
            if key == "replay" and lines > 34:  # the first screen, like `| more`
                proc.kill()
                break
            self.wfile.write(raw.replace(b"\r\n", b"\n"))
            self.wfile.flush()
        proc.wait()


# ---------------------------------------------------------------- the clips
JOB_DONE = "/: (done|finished|succeeded|error|failed)/i.test(document.getElementById('job-status').textContent)"


def clip_a0_panel(page: Page, rec: Recorder) -> None:
    page.viewport(PW, PH, PSCALE)
    page.goto(PANEL + "/")
    rec.start()
    time.sleep(1.5)
    for tab in ("Scout", "Triage", "Decision", "Actions", "Runs"):
        page.hover(".tabs button", tab)
    page.hover("#runs-table tbody tr", "ansible-runner-1548")
    page.hover("#runs-table tbody tr", "keynote-parser-91")
    time.sleep(2)
    rec.stop("a0-panel")


def clip_a_scout(page: Page, rec: Recorder) -> str:
    page.viewport(PW, PH, PSCALE)
    page.goto(PANEL + "/")
    rec.start()
    page.click(".tabs button", "Actions")
    page.type("#form-scout input[name=limit]", "8")
    page.type("#form-scout input[name=languages]", "python")
    page.click("#form-scout button[type=submit]")
    page.follow_job(600)
    time.sleep(1.5)
    page.follow_job(3)
    time.sleep(2)
    rec.stop("a-scout")
    reports = json.load(urllib.request.urlopen(PANEL + "/api/scout"))["reports"]
    return sorted(r if isinstance(r, str) else r.get("run_id", "") for r in reports)[-1]


def clip_b_triage(page: Page, rec: Recorder, scout_id: str) -> None:
    page.viewport(PW, PH, PSCALE)
    page.goto(PANEL + "/")
    rec.start()
    page.click(".tabs button", "Actions")
    page.type("#form-triage input[name=scout_run_id]", scout_id)
    page.click("#form-triage button[type=submit]")
    page.wait("!document.querySelector('#form-triage .confirm').hidden", 60)
    time.sleep(2.5)
    page.click("#form-triage .confirm")
    done = page.follow_job(16)  # the first progress lines stream in
    if not done:
        rec.pause()  # the long middle of the job is cut
        page.wait(JOB_DONE, 1800, 2)
        rec.resume()
    page.follow_job(4)
    time.sleep(2)
    page.click(".tabs button", "Triage")
    page.wait("document.querySelector('#triage-report option')", 20)
    page.js(
        "(async()=>{const s=document.getElementById('triage-report');s.value=[...s.options].map(o=>o.value)"
        ".sort().pop();s.dispatchEvent(new Event('change'));return true})()"
    )
    time.sleep(3)
    page.scroll(700, 3500)
    time.sleep(1.5)
    page.scroll(700, 3500)
    time.sleep(2)
    rec.stop("b-triage")


def clip_c_result(page: Page, rec: Recorder) -> None:
    page.viewport(PW, PH, PSCALE)
    page.goto(PANEL + "/")
    rec.start()
    page.hover("#runs-table tbody tr", "ansible-runner-1548")
    page.click(".tabs button", "Decision")
    page.js(
        "(async()=>{const s=document.getElementById('detail-run');s.value=[...s.options].map(o=>o.value)"
        ".find(v=>v.includes('ansible-runner-1548'));s.dispatchEvent(new Event('change'));return true})()"
    )
    time.sleep(3)
    for _ in range(6):
        page.scroll(520, 2600)
        time.sleep(1.6)
    rec.stop("c-result")


def clip_d_ci(page: Page, rec: Recorder) -> None:
    # Job logs and the step summary need a GitHub sign-in; the run status and job steps are public.
    page.viewport(W, H, SCALE)
    page.goto(CI_RUN, settle=4)
    rec.start()
    time.sleep(1.5)
    page.hover("*", "Success")
    time.sleep(1)
    page.click("a", "verify-bundles")
    page.wait("document.body.innerText.includes('clean-room rebuild')", 40)
    time.sleep(2)
    page.hover("*", "Verify each bundle (clean-room rebuild)")
    time.sleep(3.5)
    rec.stop("d-ci")


def clip_terminal(page: Page, rec: Recorder, name: str, steps: list[str]) -> None:
    page.viewport(W, H, SCALE)
    page.goto(f"http://127.0.0.1:{TERM_PORT}/", settle=0.8)
    rec.start()
    time.sleep(0.8)
    for key in steps:
        before = page.js("document.getElementById('t').innerText.length")
        page.js(
            f"window.__busy=true; runCmd({json.dumps(key)}, {json.dumps(COMMANDS[key][1])})"
            ".then(()=>{window.__busy=false}); true"
        )
        time.sleep(0.6 + 0.04 * len(COMMANDS[key][1]))  # the command is typed
        if key in SILENT_UNTIL_DONE:  # nothing prints until it finishes: cut the wait
            rec.pause()
            page.wait("!window.__busy", 300)
            rec.resume()
        page.wait("!window.__busy", 300)
        assert page.js("document.getElementById('t').innerText.length") > before
        time.sleep(1.5)
    time.sleep(2.5)
    rec.stop(name)


def clip_muse(page: Page, rec: Recorder, name: str, run_prefix: str) -> None:
    page.viewport(PW, PH, PSCALE)
    page.goto(CONSOLE + "/")
    rec.start()
    page.click("#runs button", run_prefix)
    time.sleep(2.5)
    page.scroll(260, 1500)  # the prompt Muse Code received
    time.sleep(1.5)
    page.scroll_to("Model activity", 2500)
    time.sleep(1.2)
    for _ in range(3):
        page.scroll(420, 2200)
        time.sleep(0.8)
    page.scroll_to("Usage and cost", 2500)
    time.sleep(3)
    rec.stop(name)


def main(which: list[str]) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    term = ThreadingHTTPServer(("127.0.0.1", TERM_PORT), TermHandler)
    threading.Thread(target=term.serve_forever, daemon=True).start()
    profile = WORK / "edge-profile"
    edge = subprocess.Popen(
        [
            EDGE,
            "--headless=new",
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}",
            f"--window-size={W},{H}",
            "--no-first-run",
            "--disable-extensions",
            "--hide-scrollbars",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        for _ in range(40):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
                ws = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("headless Edge did not start")
        cdp = CDP(ws)
        page, rec = Page(cdp), Recorder(cdp)
        everything = which == ["all"]
        if everything or "a0-panel" in which:
            clip_a0_panel(page, rec)
        scout_id = None
        if everything or "a-scout" in which:
            scout_id = clip_a_scout(page, rec)
            print("scout run:", scout_id)
        if everything or "b-triage" in which:
            if scout_id is None:
                reports = json.load(urllib.request.urlopen(PANEL + "/api/scout"))["reports"]
                scout_id = sorted(
                    r if isinstance(r, str) else r.get("run_id", "") for r in reports
                )[-1]
            clip_b_triage(page, rec, scout_id)
        if everything or "c-result" in which:
            clip_c_result(page, rec)
        if everything or "d-ci" in which:
            clip_d_ci(page, rec)
        if everything or "e-attacks" in which:
            clip_terminal(page, rec, "e-attacks", ["eval", "table"])
        if everything or "f-keyless" in which:
            clip_terminal(page, rec, "f-keyless", ["verify", "replay"])
        if everything or "g-solve" in which:
            clip_muse(page, rec, "g-solve", "solver-20261102-223630")
        if everything or "g-muse" in which:
            clip_muse(page, rec, "g-muse", "22-count-reasoning")
    finally:
        edge.terminate()
        term.shutdown()


if __name__ == "__main__":
    args = sys.argv[1:] or ["all"]
    main(args)

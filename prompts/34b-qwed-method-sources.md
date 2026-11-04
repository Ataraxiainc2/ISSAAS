# Prompt 34b — fix the method-call source regression introduced by prompt 34

Read `AGENTS.md`. Same scope as Prompt 34: work ONLY in `workspaces/upstream/qwed-verification` on branch
`issa/fix-535-taint-expressions`. No network, no push, no GitHub writes. Venv:
`workspaces/upstream/qwed-verification/.venv/Scripts/python.exe`.

Evidence (administrator, `workspaces/upstream/method_sources.py`, also reported by a reviewer bot on PR #586):
Prompt 34 item 6 changed `_check_source_call` to exact matching against `SOURCE_FUNCTIONS`. Dotted method calls are
no longer sources, which is a regression versus `main` and versus the first commit:
`cmd = sys.stdin.readline(); os.system(cmd)`, `sys.stdin.read()`, and `data = conn.recv(1024); os.system(data)` are
VULNERABLE on `main` but SAFE now.

Fix in `src/qwed_new/core/taint_analyzer.py`, smallest change: a call is a source when its full dotted name is in
`SOURCES` or `SOURCE_FUNCTIONS`, OR when its LAST dotted segment is in `SOURCE_FUNCTIONS` minus the generic `"get"`
(so `sys.stdin.readline`, `conn.recv`, `f.read`, `sock.recvfrom` are sources; `os.getcwd()` and `some_dict.get()` are
still not). Keep every other behaviour from Prompt 34.

Add `test_method_call_sources_are_detected` to `tests/test_issue_535_taint_analyzer.py` covering
`sys.stdin.readline()`, `sys.stdin.read()`, `conn.recv(1024)` (VULNERABLE) and keep `test_source_matching_is_exact_name`
passing. Run from that folder:
`.venv/Scripts/python.exe -m pytest tests/test_issue_535_taint_analyzer.py tests/test_taint_analyzer.py -q --noconftest -p no:cacheprovider --basetemp=../.tmp-qwed-p34b`,
`.venv/Scripts/python.exe ../method_sources.py` (expected: every case findings=1),
`.venv/Scripts/python.exe ../bot_cases.py` and `.venv/Scripts/python.exe ../audit_cases.py` (must be unchanged: all ok).

One local commit: `fix(taint): method-call sources match by last segment (stdin.readline, recv)`. Do not push.
Short final summary.

# Prompt 34c — `request.<anything>.get` sources lost by exact matching

Read `AGENTS.md`. Same scope as Prompts 34/34b: work ONLY in `workspaces/upstream/qwed-verification` on branch
`issa/fix-535-taint-expressions`. No network, no push, no GitHub writes. Venv:
`workspaces/upstream/qwed-verification/.venv/Scripts/python.exe`.

Evidence (administrator, `workspaces/upstream/web_get_sources.py`): on `main` (substring matching) these are
VULNERABLE; on the current branch they are SAFE — a regression from exact matching that excluded the generic `get`:
`request.cookies.get('q')`, `request.headers.get('q')`, `request.values.get('q')`, `request.query_params.get('q')`,
each assigned to `x` then `os.system(x)`. (`request.args.get`, `request.GET.get`, `os.environ.get` still work because
they are listed in `SOURCES`.)

Fix in `src/qwed_new/core/taint_analyzer.py`, smallest change: a call whose last dotted segment is `get` is a source
when the dotted name starts with `request.` (any attribute chain of the request object, e.g. `request.cookies.get`,
`request.headers.get`) or with `self.request.`. `some_dict.get()` and `os.getcwd()` must remain non-sources.
Keep everything from Prompts 34 and 34b.

Add `test_request_attribute_get_is_source` to `tests/test_issue_535_taint_analyzer.py` covering the four calls above
(VULNERABLE) and `d = {...}; d.get('q')` (SAFE). Run from that folder:
`.venv/Scripts/python.exe -m pytest tests/test_issue_535_taint_analyzer.py tests/test_taint_analyzer.py -q --noconftest -p no:cacheprovider --basetemp=../.tmp-qwed-p34c`,
`.venv/Scripts/python.exe ../web_get_sources.py` (expected: every case findings=1 except dict_get_safe findings=0),
and `../method_sources.py`, `../bot_cases.py`, `../audit_cases.py` (unchanged results).

One local commit: `fix(taint): request.<attr>.get calls are sources again`. Do not push. Short final summary.

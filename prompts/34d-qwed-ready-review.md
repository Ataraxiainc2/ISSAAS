# Prompt 34d — findings posted after PR #586 left draft (head 00bb7bf)

Read `AGENTS.md`. Same scope as Prompts 34/34b/34c: work ONLY in `workspaces/upstream/qwed-verification` on branch
`issa/fix-535-taint-expressions`. No network, no push, no GitHub writes. Venv:
`workspaces/upstream/qwed-verification/.venv/Scripts/python.exe`. Keep everything from Prompts 34, 34b and 34c.
All changes go in `src/qwed_new/core/taint_analyzer.py` plus tests in `tests/test_issue_535_taint_analyzer.py`.

Evidence (administrator, `workspaces/upstream/review2_cases.py`, which loads `main` and the branch side by side; the
two numbers are findings on main / on the branch):

1. **Request-object sources lost** (Greptile, Sentry). `cmd = request.get_data(); os.system(cmd)` 1/0 and
   `cmd = flask.request.args.get('q'); os.system(cmd)` 1/0 are regressions. `request.get_json()['cmd']` and
   `request.args.getlist('q')[0]` are 0/0 (never detected) but are the same family.
   Fix in `_check_source_call`: a call is a source when its dotted name has `request` as the first segment, or as the
   second segment after `self` or `flask`, and its last segment is `get` or starts with `get` (`get`, `get_json`,
   `get_data`, `getlist`). Keep `urllib.request.urlopen` a sink, not a source (its root is `urllib`).
   Test `test_request_object_methods_are_sources`: the four calls above, each assigned and passed to `os.system`,
   are VULNERABLE; `d = {}; x = d.get('q'); os.system(x)` stays SAFE.

2. **A sanitizer whose result is then transformed must not clean** (Greptile).
   `x = input(); y = shlex.quote(x).strip("'"); os.system(y)` is 1/0 (regression from the nested-sanitizer change);
   the inline form `os.system(shlex.quote(x).strip("'"))` is 0/0. Rule: a sanitizer call does not relieve anything
   when its result is the receiver of a method call, i.e. the sanitizer `Call` is the `.value` of an `ast.Attribute`
   whose parent is the `func` of another `Call` (`shlex.quote(x).strip(...)`, `.replace(...)`). Apply the same rule
   in both `_expr_taint_categories` (assigned form) and `_relieved_subtree_ids` (inline form) so they stay identical.
   `"ls " + shlex.quote(x)` and `f"ls {shlex.quote(x)}"` must stay SAFE for shell sinks.
   Test `test_sanitizer_result_transformed_is_not_clean`: assigned and inline `.strip("'")` forms VULNERABLE; the
   concatenation and f-string forms SAFE.

3. **Keyword arguments of sinks** (CodeRabbit). Inspecting every keyword value flags safe code (0/1 each):
   `subprocess.run(['ls'], env=os.environ.copy())`, `subprocess.run(['ls'], cwd=os.environ['HOME'])`,
   `subprocess.run(['cat'], input=d)` with `d = input()`, `requests.post(URL, json=p)` with `p = input()`.
   Add a class constant `INJECTABLE_KEYWORDS: Dict[str, Set[str]]` = shell: `{"args", "executable"}`, network:
   `{"url"}`, sql: `{"query", "operation", "sql"}`. In `_find_sinks`, keep keyword values only if the sink category is
   absent from the map (fail closed: every keyword is inspected) or the keyword name is in its set.
   Also SQL (Greptile): for `execute`/`executemany`, the query argument is the first positional argument if there is
   one, otherwise the keyword `query`/`operation`/`sql`; bound parameters are never inspected. So
   `q = input(); cursor.execute(args=(), query=q)` (0/0 today) becomes VULNERABLE.
   Store what you need (e.g. keyword names alongside `arg_nodes`) without changing the `TaintSink` fields that other
   code reads; adding an optional field with a default is fine.
   Test `test_sink_keywords_injectable_only`: the four safe calls above SAFE; `requests.get(url=u)` and
   `subprocess.run(args=c, shell=True)` with tainted `u`/`c` VULNERABLE; `cursor.execute(args=(), query=q)`
   VULNERABLE; `cursor.execute("SELECT ?", params=q)` SAFE.

4. **Starred unpacking targets** (Sentry, CodeRabbit). `prog, *args = sys.argv; os.system(args[0])` and
   `first, *rest = input().split(); os.system(rest[0])` are 0/0. In `_record_assignment` (non-pairwise branch) and in
   `_mark_source_target`, an `ast.Starred` element whose `.value` is an `ast.Name` is recorded with the whole RHS, like
   the other elements. Test `test_starred_unpacking_target_is_tainted`: both VULNERABLE.

Not in scope (do not change): reassignment to a sanitized value (`x = input(); x = shlex.quote(x); os.system(x)`)
stays reported. The analyzer is flow-insensitive and keeps the union over all assignments so that branch reassignment
cannot hide taint. That is a deliberate fail-closed trade-off and the administrator will explain it on the PR.

Run from `workspaces/upstream/qwed-verification`:
`.venv/Scripts/python.exe -m pytest tests/test_issue_535_taint_analyzer.py tests/test_taint_analyzer.py -q --noconftest -p no:cacheprovider --basetemp=../.tmp-qwed-p34d`
and, from `workspaces/upstream`, `qwed-verification/.venv/Scripts/python.exe -I review2_cases.py .cmp` after
refreshing `.cmp/ta_head.py` with your working copy of the analyzer (copy the file). Expected on the head column:
0 for the four `C2_*_SAFE` cases, 1 for every other case (`S2_reassign_sanitized` included).
Also run `../method_sources.py`, `../web_get_sources.py`, `../bot_cases.py`, `../audit_cases.py` with the venv from
the clone folder; their results must not change.

One local commit: `fix(taint): request methods, transformed sanitizers, injectable sink keywords, starred targets`.
Do not push. Short final summary.

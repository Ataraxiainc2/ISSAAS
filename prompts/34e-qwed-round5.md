# Prompt 34e — PR #586 findings on head 1dbb66a

Read `AGENTS.md`. Same scope as Prompts 34–34d: work ONLY in `workspaces/upstream/qwed-verification` on branch
`issa/fix-535-taint-expressions`. No network, no push, no GitHub writes. Venv:
`workspaces/upstream/qwed-verification/.venv/Scripts/python.exe`. Keep everything from Prompts 34–34d. Changes go in
`src/qwed_new/core/taint_analyzer.py` plus tests in `tests/test_issue_535_taint_analyzer.py`.

Evidence (administrator, `workspaces/upstream/review3_cases.py`, findings on main / 00bb7bf / 1dbb66a):

1. `os.popen(cmd=x)` with `x = input()`: 0 / 1 / 0. The shell keyword allowlist drops `cmd`. Add `cmd` and `command`
   to the shell set in `INJECTABLE_KEYWORDS`.
2. `cursor.execute(query=q, **{'args': ()})`: 0 / 1 / 0. `kw_names` uses `None` for both positional arguments and
   `**` expansions, so the expansion is taken as the positional query. Mark `**` expansions distinctly (e.g. the
   string `"**"`), never as positional. SQL query selection becomes: the first true positional argument if any;
   otherwise every `query`/`operation`/`sql` keyword AND every `**` expansion (fail closed). Bound parameters are
   still never inspected. Keep `**` expansions inspected for every other sink (as today).
3. Request reads by subscript/attribute: `request.form['cmd']`, `request.args['cmd']`, `request.cookies['c']`
   (0 / 0 / 0). Treat a direct reference whose dotted name is `request.<attr>`, `self.request.<attr>` or
   `flask.request.<attr>`, with `<attr>` in {`args`, `form`, `values`, `cookies`, `headers`, `json`, `data`, `files`,
   `GET`, `POST`, `query_params`}, as a source wherever `SOURCES` membership is tested for references (`_check_source_expr`,
   `_expr_taint_categories`, `_direct_source_reference`). Put the logic in one helper used by all three.
   `request.method` and `some_obj.form['x']` stay non-sources.
4. A sliced sanitizer result is not clean: `y = shlex.quote(x)[1:-1]; os.system(y)` is 1 / 0 / 0 (regression), and
   `os.system('ls ' + shlex.quote(x)[1:-1])` is 0 / 0 / 0. In `_transformed_call_ids`, also count a sanitizer `Call`
   that is the `.value` of an `ast.Subscript`. `'ls ' + shlex.quote(x)` must stay SAFE.

Not in scope (do not change):
- Slicing a variable that holds a sanitized value (`y = shlex.quote(x); z = y[1:-1]`). That needs per-variable
  provenance.
- The quadratic cost of long `+=` chains (0.8 s for 300).

Tests: `test_shell_command_keywords_inspected` (popen `cmd=` VULNERABLE), `test_sql_keyword_query_with_expansion`
(item 2 VULNERABLE; `cursor.execute("SELECT ?", params=q)` stays SAFE),
`test_request_subscript_reads_are_sources` (the three reads VULNERABLE, `request.method` and `obj.form['x']` SAFE),
`test_sliced_sanitizer_is_not_clean` (both item-4 forms VULNERABLE, the concatenation SAFE).

Run from `workspaces/upstream/qwed-verification`:
- `.venv/Scripts/python.exe -m pytest tests/test_issue_535_taint_analyzer.py tests/test_taint_analyzer.py -q --noconftest -p no:cacheprovider --basetemp=../.tmp-qwed-p34e`
- `../method_sources.py`, `../web_get_sources.py`, `../bot_cases.py`, `../audit_cases.py`: their results must not
  change.

One local commit: `fix(taint): popen cmd keyword, SQL ** expansion, request subscript sources, sliced sanitizers`.
No trailers in the commit message. Do not push. Short final summary.

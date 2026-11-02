# Prompt 09 — Double-anchored interpreter path in the suite command

`issa reverify 20261102-201027-psobot-keynote-parser-91` returned `ERROR`:
`suite command did not complete on baseline: $ D:\SC\ISSAAS\workspaces\<run>\keynote-parser\workspaces\<run>\keynote-parser\.venv\Scripts\python.exe ... FAILED TO START: [WinError 2]`.

The pipeline builds the suite command with an interpreter path relative to the ISSAAS root, and `_resolve_cmd` then anchors
it at the repo again. Scope: `pipeline.py`, `verifier.py`, tests. Be brief.

1. Build every interpreter path in commands as an **absolute** path (the workspace venv), and make `_resolve_cmd` leave
   absolute paths untouched and only anchor relative ones that do not already exist relative to the repo.
2. Add an offline test where the repo lives under a nested `workspaces/<run>/<repo>` folder and the pipeline's suite and
   F2P commands both resolve to the existing venv interpreter (assert the resolved path exists).
3. The suite takes ~140 s; mark the slow end-to-end tests so `uv run pytest -m "not slow"` stays fast, and keep the full
   run green.

`uv run pytest` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.

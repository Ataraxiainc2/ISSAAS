# Prompt 18 — respect SECURITY.md: private-reporting repos get a private disclosure draft, never a public one

Read `AGENTS.md` (rules 7 and 8) and `docs/BACKLOG.md` item B5. Scope: `scout.py`, `select.py`, the publish code,
`cli.py`, tests. Do not edit README or docs (the administrator does). No network in tests.

Fact found in the judging pass (2026-11-03): ansible-runner's `SECURITY.md` says vulnerability reports "MUST be submitted
by email to security@ansible.com" and "MUST NOT be reported through public channels such as GitHub issues, pull
requests". The scout never reads `SECURITY.md`, and the dry-run `publish` drafts a public PR for every repo.

1. **Scout:** fetch `SECURITY.md`, `.github/SECURITY.md`, `docs/SECURITY.md` (first found; cached and rate-limited like
   the README/CONTRIBUTING fetches). Deterministic parse into `security_policy`: `private_reporting` (true when the text
   asks not to use public issues/PRs, or to report privately, by email, or through GitHub security advisories),
   `contact` (first `security@…`-style email, or the repo's `/security/advisories/new` URL when advisories are named),
   `source` (path). Store it on the candidate and in the trace. The issue may still be fixed locally (it is already
   public), so selection does not skip it; the pick reason mentions "private disclosure required".
2. **Publish (still dry-run only):** for `private_reporting` repos never write a PR/issue/comment draft. Write
   `proof/<run_id>/disclosure.md` instead: a private report addressed to the contact, following the fields the policy
   asks for when it lists them (title, impacted project, description, affected versions, reproduction steps = the
   regression test, impact, suggested fix = `fix.patch`, disclosure status = "the issue is already public at <url>; this
   report adds a fix verified by Fail-to-Pass"), an AI-assistance disclosure line, and a header "DRAFT — for a human to
   review and send privately; ISSA AS never sends it".
3. **`issa disclosure <run_id>`:** builds `disclosure.md` for an existing run, reading `SECURITY.md` from the run's
   workspace repo if present, otherwise one GitHub contents request.
4. **Tests:** policy parse (ansible-style "MUST NOT … public", advisory-only, no policy, permissive policy), publish
   refuses public drafts for private repos and writes `disclosure.md`, disclosure content fields, AI disclosure line.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.

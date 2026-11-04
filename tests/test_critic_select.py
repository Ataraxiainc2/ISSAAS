"""Offline tests for critic.py and select.py. No real model calls."""

import json

import pytest

from issa_as import critic as critic_mod
from issa_as import traces as trace_log
from issa_as import triage as triage_mod
from issa_as import select as select_mod
from issa_as.providers import Completion


def make_scored(url: str, signals: float, solvability: float,
                runner: str = "python") -> triage_mod.TriageResult:
    final = 0.5 * signals + 0.5 * solvability
    return triage_mod.TriageResult(
        issue_url=url, repo="o/r", number=1, title="Bug", runner=runner,
        signals_score=signals, solvability=solvability, difficulty="medium",
        is_real_bug=True, security_relevant=False, rationale="r", final_score=final,
    )


class DynamicCritic:
    """Returns v2=0.1 for every issue URL found in the prompt."""

    name = "stub"
    model = "stub-model"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, *, stage: str, prompt: str, effort: str = "low",
                 run_id=None, **kwargs) -> Completion:
        assert stage == "critic"
        self.prompts.append(prompt)
        import re

        urls = re.findall(r"https://x/\d+", prompt)
        return Completion(
            text=json.dumps({"issues": [
                {"issue_url": url, "solvability_v2": 0.1,
                 "rationale_v2": "No repro; thin report."}
                for url in urls
            ]}),
            model=self.model, provider=self.name, in_tokens=10, out_tokens=20,
        )


def test_critic_only_sees_top_five() -> None:
    scored = [make_scored(f"https://x/{n}", signals=0.9 - n * 0.05, solvability=0.9)
              for n in range(8)]
    provider = DynamicCritic()
    verdicts = critic_mod.rescore(scored, provider)
    assert len(verdicts) == 5
    assert len(provider.prompts) == 1  # one batched call
    top5 = sorted(scored, key=lambda r: -r.final_score)[:5]
    assert {v.issue_url for v in verdicts} == {r.issue_url for r in top5}
    for n in range(5, 8):
        assert f"https://x/{n}" not in provider.prompts[0]


def test_critic_flags_overconfidence() -> None:
    scored = [make_scored("https://x/1", signals=0.9, solvability=0.9)]
    provider = DynamicCritic()  # v2 = 0.1, delta = -0.8
    (verdict,) = critic_mod.rescore(scored, provider)
    assert verdict.delta == pytest.approx(-0.8)
    assert verdict.flag == "overconfident"


def test_critic_ok_on_small_moves() -> None:
    scored = [make_scored("https://x/1", signals=0.9, solvability=0.2)]

    class Mild(DynamicCritic):
        def complete(self, **kwargs):
            completion = super().complete(**kwargs)
            completion.text = json.dumps({"issues": [
                {"issue_url": "https://x/1", "solvability_v2": 0.15,
                 "rationale_v2": "Agreed."}]})
            return completion

    (verdict,) = critic_mod.rescore(scored, Mild())
    assert verdict.flag == "ok"


def test_merge_recomputes_final_for_seen_only() -> None:
    scored = [
        make_scored("https://x/1", signals=0.8, solvability=0.8),  # top, seen
        make_scored("https://x/2", signals=0.0, solvability=0.0),  # unseen
    ]
    verdicts = [critic_mod.CriticVerdict(
        issue_url="https://x/1", solvability_v1=0.8, solvability_v2=0.2,
        delta=-0.6, flag="overconfident", rationale_v2="thin")]
    merged = critic_mod.merge(scored, verdicts)
    assert merged[0].solvability_v2 == pytest.approx(0.2)
    assert merged[0].critic_flag == "overconfident"
    assert merged[0].final_score == pytest.approx(0.5 * 0.8 + 0.5 * 0.2)
    assert merged[1].solvability_v2 is None
    assert merged[1].final_score == pytest.approx(0.0)


def test_rescore_emits_critic_rescored(tmp_path) -> None:
    scored = [make_scored("https://x/1", signals=0.9, solvability=0.9)]
    verdicts = critic_mod.rescore(
        scored, DynamicCritic(), run_id="r-cr", traces_dir=tmp_path
    )
    assert len(verdicts) == 1
    (event,) = [e for e in trace_log.load("r-cr", tmp_path)
                if e.type == "critic_rescored"]
    assert event.solvability_v2 == pytest.approx(0.1)
    assert event.flag == "overconfident"


def write_verdict(proof_dir, name: str, url: str, verdict: str) -> None:
    bundle = proof_dir / name
    bundle.mkdir(parents=True)
    (bundle / "verdict.json").write_text(
        json.dumps({"issue_url": url, "verdict": verdict}), encoding="utf-8")


def test_pick_skips_runner_none_and_solved(tmp_path) -> None:
    proof_dir = tmp_path / "proof"
    write_verdict(proof_dir, "run-old", "https://x/solved", "VERIFIED")
    write_verdict(proof_dir, "run-bad", "https://x/other", "NO_TEST")
    (proof_dir / "broken").mkdir()
    (proof_dir / "broken" / "verdict.json").write_text("not json", encoding="utf-8")
    scored = [
        make_scored("https://x/none", signals=0.9, solvability=0.9, runner="none"),
        make_scored("https://x/solved", signals=0.8, solvability=0.8),
        make_scored("https://x/good", signals=0.5, solvability=0.5),
    ]
    selection = select_mod.pick(scored, proof_dir=proof_dir)
    assert selection.picked is not None
    assert selection.picked.issue_url == "https://x/good"
    assert selection.picked.effort == "medium"
    reasons = {s.issue_url: s.reason for s in selection.skipped}
    assert "runner" in reasons["https://x/none"]
    assert "VERIFIED" in reasons["https://x/solved"]


def test_pick_maps_difficulty_to_effort_and_records_order(tmp_path) -> None:
    low = make_scored("https://x/low", signals=0.9, solvability=0.9)
    low.difficulty = "low"
    high = make_scored("https://x/high", signals=0.8, solvability=0.8)
    high.difficulty = "high"
    selection = select_mod.pick([high, low], proof_dir=tmp_path / "proof")
    assert selection.picked is not None
    assert selection.picked.issue_url == "https://x/low"
    assert selection.picked.effort == "low"
    assert selection.skipped[0].issue_url == "https://x/high"
    assert "lower score" in selection.skipped[0].reason


def test_pick_none_when_nothing_qualifies(tmp_path) -> None:
    scored = [make_scored("https://x/1", signals=0.5, solvability=0.5, runner="none")]
    selection = select_mod.pick(scored, proof_dir=tmp_path / "proof")
    assert selection.picked is None
    assert len(selection.skipped) == 1


def test_pick_emits_selected_and_skipped(tmp_path) -> None:
    scored = [
        make_scored("https://x/good", signals=0.7, solvability=0.7),
        make_scored("https://x/none", signals=0.9, solvability=0.9, runner="none"),
    ]
    selection = select_mod.pick(
        scored, proof_dir=tmp_path / "proof", run_id="r-sel", traces_dir=tmp_path
    )
    assert selection.picked is not None
    events = trace_log.load("r-sel", tmp_path)
    selected = [e for e in events if e.type == "selected"]
    skipped = [e for e in events if e.type == "skipped"]
    assert len(selected) == 1 and selected[0].issue_id == "https://x/good"
    assert len(skipped) == 1 and skipped[0].issue_id == "https://x/none"


def test_solved_issue_urls_ignores_missing_dir(tmp_path) -> None:
    assert select_mod.solved_issue_urls(tmp_path / "nope") == set()


def test_critic_prompt_carries_security_class_and_delimited_excerpt() -> None:
    scored = [make_scored("https://x/1", signals=0.9, solvability=0.9)]
    scored[0].security_class = "sql-injection"
    scored[0].body_excerpt = "login query is injectable"
    prompt = critic_mod.build_prompt(scored)
    assert '"security_class": "sql-injection"' in prompt
    assert "None" not in prompt.split('"security_class"')[1][:30]
    assert triage_mod.EXCERPT_START in prompt
    assert triage_mod.EXCERPT_END in prompt
    assert "login query is injectable" in prompt
    assert "untrusted" in prompt.lower()


def test_critic_retries_truncated_answer_once() -> None:
    from issa_as.providers import Completion

    good = json.dumps({"issues": [
        {"issue_url": "https://x/1", "solvability_v2": 0.5,
         "rationale_v2": "Second look agrees."}]})

    class Flaky:
        name = "flaky"
        model = "flaky-model"

        def __init__(self) -> None:
            self.calls = 0
            self.max_tokens_seen: list[int] = []

        def complete(self, **kwargs) -> Completion:
            self.calls += 1
            self.max_tokens_seen.append(kwargs.get("max_tokens"))
            if self.calls == 1:
                return Completion(
                    text='{"issues": [', model=self.model, provider=self.name,
                    truncated=True, finish_reason="length",
                )
            return Completion(text=good, model=self.model, provider=self.name)

    provider = Flaky()
    scored = [make_scored("https://x/1", signals=0.9, solvability=0.9)]
    (verdict,) = critic_mod.rescore(scored, provider)
    assert verdict.solvability_v2 == pytest.approx(0.5)
    assert provider.calls == 2
    assert provider.max_tokens_seen[1] > provider.max_tokens_seen[0]


def test_pick_skips_non_bugs_and_counts_them(tmp_path) -> None:
    real = make_scored("https://x/real", signals=0.9, solvability=0.9)
    noise = make_scored("https://x/noise", signals=0.99, solvability=0.99)
    noise.is_real_bug = False
    selection = select_mod.pick([noise, real], proof_dir=tmp_path / "proof")
    assert selection.picked is not None
    assert selection.picked.issue_url == "https://x/real"
    assert selection.skipped_not_bug == 1
    (skipped,) = [s for s in selection.skipped if s.issue_url == "https://x/noise"]
    assert "not a real bug" in skipped.reason


def test_pick_only_python_runner_is_attemptable(tmp_path) -> None:
    node = make_scored("https://x/node", signals=0.9, solvability=0.9, runner="node")
    selection = select_mod.pick([node], proof_dir=tmp_path / "proof")
    assert selection.picked is None
    assert selection.skipped[0].reason == "runner not implemented yet (node)"
    assert selection.skipped_not_bug == 0


@pytest.mark.parametrize(
    ("difficulty", "security_class", "model", "effort"),
    [
        ("low", None, "muse-spark-1.3-contributor", "low"),
        ("medium", None, "muse-spark-1.3-contributor", "medium"),
        ("medium", "xss", "muse-spark-1.3-contributor", "medium"),
        ("medium", "ssrf", "muse-spark-1.3-contributor", "medium"),
        ("high", None, "muse-spark-1.3", "high"),
        ("high", "xss", "muse-spark-1.3", "high"),
        ("low", "command-injection", "muse-spark-1.3", "high"),
        ("medium", "sql-injection", "muse-spark-1.3", "high"),
        ("low", "path-traversal", "muse-spark-1.3", "high"),
        ("medium", "unsafe-deserialization", "muse-spark-1.3", "high"),
        ("low", "auth-bypass", "muse-spark-1.3", "high"),
    ],
)
def test_solver_routing_rule_table(difficulty, security_class, model, effort) -> None:
    got_model, got_effort, rationale = select_mod.route_solver(difficulty, security_class)
    assert (got_model, got_effort) == (model, effort)
    assert isinstance(rationale, str) and len(rationale.splitlines()) == 1
    assert rationale  # one-line rationale always present


def test_pick_carries_recommended_solver_routing(tmp_path) -> None:
    hard = make_scored("https://x/hard", signals=0.9, solvability=0.9)
    hard.difficulty = "high"
    hard.security_class = "path-traversal"
    selection = select_mod.pick([hard], proof_dir=tmp_path / "proof")
    assert selection.picked is not None
    assert selection.picked.recommended_solver_model == "muse-spark-1.3"
    assert selection.picked.recommended_effort == "high"
    assert "path-traversal" in selection.picked.model_rationale
    easy = make_scored("https://x/easy", signals=0.9, solvability=0.9)
    easy.difficulty = "low"
    calm = select_mod.pick([easy], proof_dir=tmp_path / "proof")
    assert calm.picked is not None
    assert calm.picked.recommended_solver_model == "muse-spark-1.3-contributor"
    assert calm.picked.recommended_effort == "low"


def write_attempt_bundle(proof_dir, name: str, url: str | None, verdict: str,
                         *, verdict_has_url: bool = True,
                         linux: dict | None = None) -> None:
    """One fake proof bundle: verdict.json (+issue.json/linux-verify.json)."""
    bundle = proof_dir / name
    bundle.mkdir(parents=True, exist_ok=True)
    verdict_doc: dict = {"run_id": name, "verdict": verdict}
    if verdict_has_url and url is not None:
        verdict_doc["issue_url"] = url
    (bundle / "verdict.json").write_text(json.dumps(verdict_doc), encoding="utf-8")
    if url is not None and not verdict_has_url:
        (bundle / "issue.json").write_text(
            json.dumps({"issue_url": url}), encoding="utf-8")
    if linux is not None:
        (bundle / "linux-verify.json").write_text(
            json.dumps({"run_id": name, **linux}), encoding="utf-8")


def test_linux_verified_bundle_counts_as_solved(tmp_path) -> None:
    url = "https://github.com/ansible/ansible-runner/issues/1548"
    proof_dir = tmp_path / "proof"
    # Windows verdict has no issue_url and says ERROR (fcntl is missing);
    # the Linux CI re-verification passed cleanly.
    write_attempt_bundle(proof_dir, "run-linux", url, "ERROR",
                         verdict_has_url=False,
                         linux={"verdict": "VERIFIED", "f2p_pass": True,
                                "regressions": 0})
    assert select_mod.solved_issue_urls(proof_dir) == {url}
    selection = select_mod.pick(
        [make_scored(url, signals=0.9, solvability=0.9)], proof_dir=proof_dir)
    assert selection.picked is None
    (skipped,) = selection.skipped
    assert "already solved" in skipped.reason
    assert "VERIFIED" in skipped.reason
    assert "Linux CI" in skipped.reason


def test_linux_verify_with_regressions_is_not_solved(tmp_path) -> None:
    url = "https://github.com/o/r/issues/9"
    proof_dir = tmp_path / "proof"
    write_attempt_bundle(proof_dir, "run-bad", url, "ERROR",
                         verdict_has_url=False,
                         linux={"verdict": "VERIFIED", "f2p_pass": True,
                                "regressions": 2})
    assert select_mod.solved_issue_urls(proof_dir) == set()
    assert select_mod.attempted_issues(proof_dir)[url][0] == "ERROR"


def test_local_verified_reason_names_local_evidence(tmp_path) -> None:
    url = "https://github.com/o/r/issues/7"
    proof_dir = tmp_path / "proof"
    write_verdict(proof_dir, "run-old", url, "VERIFIED")
    selection = select_mod.pick(
        [make_scored(url, signals=0.9, solvability=0.9)], proof_dir=proof_dir)
    assert selection.picked is None
    (skipped,) = selection.skipped
    assert "already solved" in skipped.reason
    assert "VERIFIED" in skipped.reason
    assert "local" in skipped.reason


def test_attempted_bundle_skipped_with_verdict_in_reason(tmp_path) -> None:
    proof_dir = tmp_path / "proof"
    write_verdict(proof_dir, "run-err", "https://x/attempted", "REGRESSION")
    scored = [
        make_scored("https://x/attempted", signals=0.9, solvability=0.9),
        make_scored("https://x/fresh", signals=0.5, solvability=0.5),
    ]
    selection = select_mod.pick(scored, proof_dir=proof_dir)
    assert selection.picked is not None
    assert selection.picked.issue_url == "https://x/fresh"
    reasons = {s.issue_url: s.reason for s in selection.skipped}
    assert reasons["https://x/attempted"] == (
        "already attempted (REGRESSION, run run-err)")


def test_retry_attempted_reallows_unsolved_but_never_solved(tmp_path) -> None:
    proof_dir = tmp_path / "proof"
    write_verdict(proof_dir, "run-err", "https://x/attempted", "ERROR")
    write_verdict(proof_dir, "run-ok", "https://x/solved", "VERIFIED")
    scored = [
        make_scored("https://x/solved", signals=0.95, solvability=0.95),
        make_scored("https://x/attempted", signals=0.9, solvability=0.9),
        make_scored("https://x/fresh", signals=0.1, solvability=0.1),
    ]
    plain = select_mod.pick(scored, proof_dir=proof_dir)
    assert plain.picked is not None
    assert plain.picked.issue_url == "https://x/fresh"
    retried = select_mod.pick(scored, proof_dir=proof_dir, retry_attempted=True)
    assert retried.picked is not None
    assert retried.picked.issue_url == "https://x/attempted"
    reasons = {s.issue_url: s.reason for s in retried.skipped}
    assert "already solved" in reasons["https://x/solved"]
    assert "already attempted" not in reasons.get("https://x/attempted", "")

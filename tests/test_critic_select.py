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

"""Offline tests for Prompts 28/30: EN/ES/PT candidate briefs + live panel view.

Two passes: English-only briefs, then one batched ES/PT translation call
with honest fallback (missing translations stay missing, never English
copies). No network, no model calls: FakeProvider and stage stubs only.
"""

from __future__ import annotations

import io
import json
from email.message import Message
from pathlib import Path

import pytest

from issa_as import traces as trace_log
from issa_as import briefs as briefs_mod
from issa_as.budget import cost_usd
from issa_as.providers import Completion, FakeProvider, FailingProvider

ISSUE_URL = "https://github.com/o/r/issues/1"
ISSUE_URL_2 = "https://github.com/o/r/issues/2"


def make_candidate(url: str = ISSUE_URL, **overrides) -> dict:
    info = {
        "issue_url": url,
        "repo": "o/r",
        "number": 1,
        "title": "崩溃：空输入",
        "labels": ["bug"],
        "body_excerpt": "复现步骤：运行它。",
    }
    info.update(overrides)
    return info


def brief_entry(url: str = ISSUE_URL, **overrides) -> dict:
    entry = {
        "issue_url": url,
        "title": {"en": "Crash on empty input", "es": "Fallo con entrada vacía",
                  "pt": "Falha com entrada vazia"},
        "problem": {"en": "The app crashes on empty input. Users lose data.",
                     "es": "La app falla con entrada vacía.",
                     "pt": "O app falha com entrada vazia."},
        "fix_idea": {"en": "Guard against empty input before parsing.",
                      "es": "Validar la entrada vacía.",
                      "pt": "Validar a entrada vazia."},
        "source_language": "zh",
    }
    entry.update(overrides)
    return entry


def brief_answer(entries: list[dict]) -> str:
    return json.dumps({"issues": entries})


class RecordingStub:
    """Stage-dispatched stub: records calls, returns canned JSON per stage."""

    name = "stub"
    model = "stub-model"

    def __init__(self, by_stage: dict[str, str]) -> None:
        self.by_stage = by_stage
        self.calls: list[dict] = []

    def complete(self, *, stage: str, prompt: str, effort: str = "low",
                 run_id=None, **kwargs) -> Completion:
        self.calls.append({"stage": stage, "prompt": prompt, "effort": effort})
        return Completion(
            text=self.by_stage[stage], model=self.model, provider=self.name,
            in_tokens=10, out_tokens=20,
        )


class ScriptedProvider(FakeProvider):
    """FakeProvider returning canned texts in call order, then failing."""

    def __init__(self, texts: list[str], **kwargs) -> None:
        super().__init__(text=texts[0] if texts else "", **kwargs)
        self.texts = list(texts)
        self.calls = 0

    def complete(self, **kwargs):
        from issa_as.providers import ProviderError

        if self.calls >= len(self.texts):
            raise ProviderError(self.name, "no more scripted texts")
        self.text = self.texts[self.calls]
        self.calls += 1
        return super().complete(**kwargs)


def brief_en_answer(entries: list[dict]) -> str:
    """Pass-1 fixture: English-only briefs for the given entries."""
    return json.dumps({"issues": [{
        "issue_url": e["issue_url"],
        "title": {"en": (e.get("title") or {}).get("en", "")},
        "problem": {"en": (e.get("problem") or {}).get("en", "")},
        "fix_idea": {"en": (e.get("fix_idea") or {}).get("en", "")},
        "source_language": e.get("source_language"),
    } for e in entries]})


def brief_translation_answer(entries: list[dict]) -> str:
    """Pass-2 fixture: ES/PT translations for the given entries."""
    return json.dumps({"issues": [{
        "issue_url": e["issue_url"],
        "title": {"es": (e.get("title") or {}).get("es"),
                  "pt": (e.get("title") or {}).get("pt")},
        "problem": {"es": (e.get("problem") or {}).get("es"),
                    "pt": (e.get("problem") or {}).get("pt")},
        "fix_idea": {"es": (e.get("fix_idea") or {}).get("es"),
                     "pt": (e.get("fix_idea") or {}).get("pt")},
    } for e in entries]})


def brief_echo_translation(entries: list[dict]) -> str:
    """Pass-2 fixture: ES/PT echo the English text (untranslated answer)."""
    return json.dumps({"issues": [{
        "issue_url": e["issue_url"],
        "title": {"es": (e.get("title") or {}).get("en", ""),
                  "pt": (e.get("title") or {}).get("en", "")},
        "problem": {"es": (e.get("problem") or {}).get("en", ""),
                    "pt": (e.get("problem") or {}).get("en", "")},
        "fix_idea": {"es": (e.get("fix_idea") or {}).get("en", ""),
                     "pt": (e.get("fix_idea") or {}).get("en", "")},
    } for e in entries]})


# -- prompt ---------------------------------------------------------------


def test_prompt_sends_title_labels_and_body_excerpt() -> None:
    long_body = "A" * 1400 + "B" * 500  # well over the 1500-char budget
    prompt = briefs_mod.build_prompt([make_candidate(body_excerpt=long_body)])
    assert "崩溃：空输入" in prompt
    assert "bug" in prompt
    from issa_as import triage as triage_mod

    assert triage_mod.EXCERPT_START in prompt
    assert triage_mod.EXCERPT_END in prompt
    assert "untrusted" in prompt.lower()
    # Body excerpt is capped at ~1500 chars: the boundary slice stays,
    # anything past it is cut.
    assert "A" * 1400 + "B" * 100 in prompt
    assert "B" * 101 not in prompt


def test_prompt_asks_for_english_only_json_per_issue() -> None:
    prompt = briefs_mod.build_prompt([make_candidate()])
    assert ISSUE_URL in prompt
    for token in ('"en"', '"source_language"', '"fix_idea"', '"problem"'):
        assert token in prompt
    # Pass 1 is English only: no translation languages anywhere.
    assert '"es"' not in prompt
    assert '"pt"' not in prompt
    assert "ENGLISH ONLY" in prompt


def test_prompt_shows_exact_nested_shape_example() -> None:
    prompt = briefs_mod.build_prompt([make_candidate()])
    lines = prompt.splitlines()
    idx = next(i for i, line in enumerate(lines)
               if line.startswith("Example answer"))
    doc = json.loads(lines[idx + 1])
    assert set(doc) == {"issues"}
    assert set(doc["issues"][0]) == {
        "issue_url", "title", "problem", "fix_idea", "source_language"}
    assert doc["issues"][0]["title"] == {"en": "Crash on empty input"}
    assert set(doc["issues"][0]["problem"]) == {"en"}


def test_translate_prompt_asks_for_es_pt_with_english_source() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_en_answer([brief_entry()]), [make_candidate()])
    prompt = briefs_mod.build_translate_prompt([entry])
    assert ISSUE_URL in prompt
    assert "Crash on empty input" in prompt  # validated English is translated
    assert '"es"' in prompt and '"pt"' in prompt
    assert "never " in prompt.lower() and "copy the English text" in prompt


def test_translate_prompt_shows_exact_nested_shape_example() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_en_answer([brief_entry()]), [make_candidate()])
    prompt = briefs_mod.build_translate_prompt([entry])
    lines = prompt.splitlines()
    idx = next(i for i, line in enumerate(lines)
               if line.startswith("Example answer"))
    doc = json.loads(lines[idx + 1])
    item = doc["issues"][0]
    assert set(item) == {"issue_url", "title", "problem", "fix_idea"}
    assert set(item["title"]) == {"es", "pt"}
    assert set(item["problem"]) == {"es", "pt"}


# -- parsing and validation -----------------------------------------------


def test_parse_valid_briefs() -> None:
    # Pass 1 keeps English only; translations stay missing for pass 2.
    (entry,) = briefs_mod.parse_response(
        brief_answer([brief_entry()]), [make_candidate()])
    assert entry.issue_url == ISSUE_URL
    assert entry.title == {"en": "Crash on empty input", "es": None, "pt": None}
    assert entry.problem["en"] == "The app crashes on empty input. Users lose data."
    assert entry.problem["es"] is None and entry.problem["pt"] is None
    assert entry.fix_idea["en"] == "Guard against empty input before parsing."
    assert entry.missing_languages == []
    assert entry.source_language == "zh"


def test_parse_translation_fills_es_pt() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_en_answer([brief_entry()]), [make_candidate()])
    table = briefs_mod.parse_translation_response(
        brief_translation_answer([brief_entry()]), [entry])
    briefs_mod.apply_translations([entry], table)
    assert entry.title["es"] == "Fallo con entrada vacía"
    assert entry.title["pt"] == "Falha com entrada vazia"
    assert entry.problem["es"] == "La app falla con entrada vacía."
    assert entry.fix_idea["pt"] == "Validar a entrada vazia."
    assert entry.title["en"] == "Crash on empty input"  # English untouched
    assert entry.missing_languages == []


def test_parse_translation_drops_unknown_urls() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_en_answer([brief_entry()]), [make_candidate()])
    table = briefs_mod.parse_translation_response(
        brief_translation_answer(
            [brief_entry("https://github.com/o/r/issues/999")]),
        [entry],
    )
    briefs_mod.apply_translations([entry], table)
    assert entry.title["es"] is None and entry.title["pt"] is None
    assert entry.missing_languages == ["es", "pt"]


def test_parse_translation_rejects_non_json_and_wrong_shape() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_en_answer([brief_entry()]), [make_candidate()])
    with pytest.raises(ValueError, match="not JSON"):
        briefs_mod.parse_translation_response("nope", [entry])
    with pytest.raises(ValueError, match="must be an object"):
        briefs_mod.parse_translation_response('{"issues": {}}', [entry])


def test_partial_translation_keeps_translated_fields() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_en_answer([brief_entry()]), [make_candidate()])
    partial = dict(brief_entry())
    partial["title"] = {"es": "Fallo con entrada vacía", "pt": "Crash on empty input"}
    partial["problem"] = {"es": "", "pt": ""}
    partial["fix_idea"] = {}
    table = briefs_mod.parse_translation_response(
        brief_translation_answer([partial]), [entry])
    briefs_mod.apply_translations([entry], table)
    # Translated fields keep their text; empty or English-identical ones
    # stay missing; a language with nothing translated is missing.
    assert entry.title["es"] == "Fallo con entrada vacía"
    assert entry.title["pt"] is None
    assert entry.problem["es"] is None
    assert entry.missing_languages == ["pt"]


def test_parse_trims_and_caps_strings() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_answer([brief_entry(
            title={"en": "  " + "t" * 200 + "  "},
            problem={"en": "p" * 500},
            fix_idea={"en": "f" * 300},
        )]),
        [make_candidate()],
    )
    assert len(entry.title["en"]) == briefs_mod.BRIEF_TITLE_CHARS
    assert len(entry.problem["en"]) == briefs_mod.BRIEF_PROBLEM_CHARS
    assert len(entry.fix_idea["en"]) == briefs_mod.BRIEF_FIX_CHARS
    assert entry.title["en"] == "t" * briefs_mod.BRIEF_TITLE_CHARS
    # Pass 1 never fills translations, even when English is capped.
    assert entry.title["es"] is None and entry.title["pt"] is None


def test_parse_drops_unknown_urls() -> None:
    entries = briefs_mod.parse_response(
        brief_answer([brief_entry("https://github.com/o/r/issues/999"),
                      brief_entry()]),
        [make_candidate()],
    )
    assert [e.issue_url for e in entries] == [ISSUE_URL]


def test_parse_pass1_leaves_translations_missing() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_answer([brief_entry(title={"en": "Only English"},
                                  problem={"es": "Solo español"},
                                  fix_idea={})]),
        [make_candidate()],
    )
    # Translations stay missing (None): only pass 2 may fill them, so
    # English is never copied into es/pt.
    assert entry.title == {"en": "Only English", "es": None, "pt": None}
    # Missing English falls back to the first available language.
    assert entry.problem["en"] == "Solo español"
    assert entry.problem["es"] is None and entry.problem["pt"] is None
    # A wholly missing field keeps an empty English text (only a missing
    # *entry* falls back to the candidate's own title, so every candidate
    # keeps a brief).
    assert entry.fix_idea == {"en": "", "es": None, "pt": None}


def test_parse_missing_entry_falls_back_to_candidate_title() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_answer([]), [make_candidate()])
    assert entry.issue_url == ISSUE_URL
    assert entry.title == {"en": "崩溃：空输入", "es": None, "pt": None}
    assert entry.source_language == "unknown"


def test_parse_normalizes_source_language() -> None:
    (entry,) = briefs_mod.parse_response(
        brief_answer([brief_entry(source_language=" ZH ")]),
        [make_candidate()],
    )
    assert entry.source_language == "zh"


def test_parse_rejects_non_json_and_wrong_shape() -> None:
    with pytest.raises(ValueError, match="not JSON"):
        briefs_mod.parse_response("nope", [make_candidate()])
    with pytest.raises(ValueError, match="must be an object"):
        briefs_mod.parse_response('{"issues": {}}', [make_candidate()])


def test_brief_calls_use_low_effort_and_brief_stage(tmp_path) -> None:
    provider = RecordingStub({"brief": brief_answer([brief_entry()])})
    briefs_mod.brief_candidates(
        [make_candidate()], provider, scout_run_id="s1", run_id="brief-s1",
        traces_dir=tmp_path / "traces", scout_dir=tmp_path / "scout",
    )
    # Two passes: English briefs, then ES/PT translation.
    assert len(provider.calls) == 2
    for call in provider.calls:
        assert call["stage"] == "brief"
        assert call["effort"] == "low"  # explicit reasoning_effort=low


def test_brief_schemas_are_strict() -> None:
    for schema in (briefs_mod.BRIEF_EN_SCHEMA, briefs_mod.TRANSLATE_SCHEMA):
        assert schema["additionalProperties"] is False
        (item_schema,) = [schema["properties"]["issues"]["items"]]
        assert item_schema["additionalProperties"] is False
        required = set(item_schema["required"])
        assert {"issue_url", "title", "problem", "fix_idea"} <= required
        for name, prop in item_schema["properties"].items():
            if name not in required:
                types = prop["type"] if isinstance(prop["type"], list) else [prop["type"]]
                assert "null" in types, f"optional {name!r} must be nullable"


def test_dry_run_makes_zero_network_calls() -> None:
    class Exploding:
        name = "boom"
        model = "boom-model"

        def complete(self, **kwargs):
            raise AssertionError("provider must not be called on dry-run")

    info = briefs_mod.dry_run_info([make_candidate()], Exploding())
    assert info["model"] == "boom-model"
    assert info["n_candidates"] == 1
    assert info["est_in_tokens"] > 0 and info["est_out_tokens"] > 0


# -- build_briefs: file + trace -------------------------------------------


def _write_scout_report(scout_dir: Path, run_id: str,
                        candidates: list[dict]) -> None:
    scout_dir.mkdir(parents=True, exist_ok=True)
    (scout_dir / f"{run_id}.json").write_text(
        json.dumps({"run_id": run_id, "candidates": candidates}),
        encoding="utf-8",
    )


def test_build_briefs_writes_file_and_trace(tmp_path) -> None:
    scout_dir = tmp_path / "scout"
    traces_dir = tmp_path / "traces"
    _write_scout_report(scout_dir, "s1", [make_candidate(), make_candidate(ISSUE_URL_2)])
    provider = FakeProvider(text=brief_answer([brief_entry(), brief_entry(ISSUE_URL_2)]))
    briefs = briefs_mod.build_briefs(
        "s1", provider=provider, run_id="brief-s1",
        traces_dir=traces_dir, scout_dir=scout_dir,
    )
    assert set(briefs) == {ISSUE_URL, ISSUE_URL_2}
    assert briefs[ISSUE_URL]["missing_languages"] == []
    doc = json.loads((scout_dir / "s1.briefs.json").read_text(encoding="utf-8"))
    assert doc["scout_run_id"] == "s1"
    assert doc["run_id"] == "brief-s1"
    assert doc["error"] == ""
    assert doc["translated"] is True
    assert doc["briefs"][ISSUE_URL]["title"]["pt"] == "Falha com entrada vazia"
    assert doc["briefs"][ISSUE_URL]["missing_languages"] == []
    events = trace_log.load("brief-s1", traces_dir)
    briefed = [e for e in events if e.type == "candidates_briefed"]
    assert len(briefed) == 1
    assert briefed[0].count == 2
    assert briefed[0].model == provider.model
    assert briefed[0].provider == provider.name
    # Both passes are logged with tokens and cost and counted here.
    assert briefed[0].in_tokens == 2 * provider.in_tokens
    assert briefed[0].out_tokens == 2 * provider.out_tokens
    assert briefed[0].error == ""
    assert briefed[0].translated is True
    assert (briefed[0].es_count, briefed[0].pt_count) == (2, 2)
    calls = [e for e in events if e.type == "model_call"]
    assert len(calls) == 2
    assert {c.stage for c in calls} == {"brief"}


def test_pass2_output_fills_es_pt(tmp_path) -> None:
    provider = ScriptedProvider([
        brief_en_answer([brief_entry()]),
        brief_translation_answer([brief_entry()]),
    ])
    out = briefs_mod.brief_candidates(
        [make_candidate()], provider, scout_run_id="s1", run_id="brief-s1",
        traces_dir=tmp_path / "traces", scout_dir=tmp_path / "scout",
    )
    assert out[ISSUE_URL]["title"]["es"] == "Fallo con entrada vacía"
    assert out[ISSUE_URL]["title"]["pt"] == "Falha com entrada vazia"
    assert out[ISSUE_URL]["missing_languages"] == []
    doc = json.loads((tmp_path / "scout" / "s1.briefs.json").read_text(encoding="utf-8"))
    assert doc["translated"] is True
    events = trace_log.load("brief-s1", tmp_path / "traces")
    briefed = [e for e in events if e.type == "candidates_briefed"]
    assert briefed[0].translated is True
    assert (briefed[0].es_count, briefed[0].pt_count) == (1, 1)
    assert briefed[0].error == ""
    calls = [e for e in events if e.type == "model_call"]
    assert len(calls) == 2


def test_pass2_failure_keeps_english_with_missing_languages(tmp_path) -> None:
    # Only one scripted text: the pass-2 call raises, like a live failure.
    provider = ScriptedProvider([brief_en_answer([brief_entry()])])
    out = briefs_mod.brief_candidates(
        [make_candidate()], provider, scout_run_id="s1", run_id="brief-s1",
        traces_dir=tmp_path / "traces", scout_dir=tmp_path / "scout",
    )
    assert out[ISSUE_URL]["title"] == {
        "en": "Crash on empty input", "es": None, "pt": None}
    assert out[ISSUE_URL]["missing_languages"] == ["es", "pt"]
    doc = json.loads((tmp_path / "scout" / "s1.briefs.json").read_text(encoding="utf-8"))
    assert doc["translated"] is False
    assert doc["error"] == ""  # pass 1 succeeded: English briefs are real
    assert doc["briefs"][ISSUE_URL]["title"]["en"] == "Crash on empty input"
    events = trace_log.load("brief-s1", tmp_path / "traces")
    briefed = [e for e in events if e.type == "candidates_briefed"]
    assert briefed[0].count == 1
    assert briefed[0].translated is False
    assert (briefed[0].es_count, briefed[0].pt_count) == (0, 0)
    assert briefed[0].error != ""


def test_english_only_pass2_answer_treated_as_missing(tmp_path) -> None:
    provider = ScriptedProvider([
        brief_en_answer([brief_entry()]),
        brief_echo_translation([brief_entry()]),
    ])
    out = briefs_mod.brief_candidates(
        [make_candidate()], provider, scout_run_id="s1", run_id="brief-s1",
        traces_dir=tmp_path / "traces", scout_dir=tmp_path / "scout",
    )
    # es/pt echo English: treated as missing, never stored as translations.
    assert out[ISSUE_URL]["title"]["es"] is None
    assert out[ISSUE_URL]["title"]["pt"] is None
    assert out[ISSUE_URL]["missing_languages"] == ["es", "pt"]
    doc = json.loads((tmp_path / "scout" / "s1.briefs.json").read_text(encoding="utf-8"))
    assert doc["translated"] is False
    events = trace_log.load("brief-s1", tmp_path / "traces")
    briefed = [e for e in events if e.type == "candidates_briefed"]
    assert briefed[0].translated is False
    assert (briefed[0].es_count, briefed[0].pt_count) == (0, 0)
    assert briefed[0].error == ""  # pass 2 parsed fine; it just held no translations


def test_failed_call_recorded_without_raising(tmp_path) -> None:
    scout_dir = tmp_path / "scout"
    traces_dir = tmp_path / "traces"
    _write_scout_report(scout_dir, "s1", [make_candidate()])
    briefs = briefs_mod.build_briefs(
        "s1", provider=FailingProvider(), run_id="brief-s1",
        traces_dir=traces_dir, scout_dir=scout_dir,
    )
    assert briefs == {}
    doc = json.loads((scout_dir / "s1.briefs.json").read_text(encoding="utf-8"))
    assert doc["briefs"] == {}
    assert "boom" in doc["error"]
    events = trace_log.load("brief-s1", traces_dir)
    briefed = [e for e in events if e.type == "candidates_briefed"]
    assert len(briefed) == 1
    assert briefed[0].count == 0
    assert briefed[0].error != ""


def test_build_briefs_rejects_missing_or_empty_report(tmp_path) -> None:
    scout_dir = tmp_path / "scout"
    scout_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="no such scout report"):
        briefs_mod.build_briefs("nope", run_id="brief-nope", scout_dir=scout_dir)
    _write_scout_report(scout_dir, "empty", [])
    with pytest.raises(ValueError, match="no candidates"):
        briefs_mod.build_briefs("empty", run_id="brief-empty", scout_dir=scout_dir)


# -- CLI: issa brief -------------------------------------------------------


def test_cli_brief_missing_report(monkeypatch, tmp_path) -> None:
    from issa_as import cli as cli_mod

    monkeypatch.chdir(tmp_path)
    assert cli_mod.run_brief("nope") == 2
    assert cli_mod.main(["brief", "nope"]) == 2


def test_cli_brief_happy_path(monkeypatch, tmp_path, capsys) -> None:
    from issa_as import cli as cli_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "scout").mkdir()
    _write_scout_report(tmp_path / "scout", "s1", [make_candidate()])
    provider = FakeProvider(text=brief_answer([brief_entry()]))
    assert cli_mod.run_brief("s1", provider=provider) == 0
    out = capsys.readouterr().out
    assert "1 briefs in scout/s1.briefs.json" in out
    assert (tmp_path / "scout" / "s1.briefs.json").is_file()


def test_cli_brief_parser() -> None:
    from issa_as.cli import build_parser

    args = build_parser().parse_args(["brief", "s1"])
    assert args.command == "brief"
    assert args.scout_run_id == "s1"


# -- run loop: briefs right after scouting ----------------------------------


class FakeGitHub:
    """Minimal scout client: one python repo with a test suite."""

    def __init__(self, issues=None) -> None:
        self.issues = issues if issues is not None else [make_issue()]
        self.queries: list[str] = []

    def search_issues(self, query: str) -> list[dict]:
        self.queries.append(query)
        return list(self.issues)

    def get_repo(self, full_name: str) -> dict:
        return {"archived": False, "fork": False, "stargazers_count": 120,
                "size": 2048, "pushed_at": "2026-10-20T00:00:00+00:00"}

    def get_text(self, full_name: str, path: str):
        return None

    def list_dir(self, full_name: str, path: str) -> list:
        return []

    def linked_open_pr(self, full_name: str, number: int) -> bool:
        return False


def make_issue(number: int = 1) -> dict:
    return {
        "html_url": f"https://github.com/o/r/issues/{number}",
        "repository_full_name": "o/r",
        "number": number,
        "title": "Bug: crash on empty input",
        "body": "Steps to reproduce:\n1. Run it",
        "labels": [{"name": "bug"}],
        "user": {"login": "human"},
        "comments": 3,
        "created_at": "2026-10-29T00:00:00+00:00",
    }


class RunStub:
    """Triage/critic/brief stub; triage judges the issue not a real bug."""

    name = "stub"
    model = "muse-spark-1.3-contributor"

    def __init__(self, fail_brief: bool = False) -> None:
        self.fail_brief = fail_brief
        self.calls: list[dict] = []

    def complete(self, *, stage, prompt, effort="low", run_id=None, **kwargs):
        from issa_as.providers import ProviderError

        self.calls.append({"stage": stage, "prompt": prompt, "effort": effort})
        if stage == "brief" and self.fail_brief:
            raise ProviderError(self.name, "boom")
        if stage == "brief":
            text = brief_answer([brief_entry()])
        elif stage == "triage":
            text = json.dumps({"issues": [{
                "issue_url": ISSUE_URL, "solvability": 0.8,
                "difficulty": "low", "is_real_bug": False,
                "security_relevant": False, "rationale": "Feature request.",
            }]})
        else:
            text = json.dumps({"issues": [{
                "issue_url": ISSUE_URL, "solvability_v2": 0.75,
                "rationale_v2": "Agreed, not a bug.",
            }]})
        return Completion(
            text=text, model=self.model, provider=self.name,
            in_tokens=100, out_tokens=50,
            cost_usd=cost_usd(self.model, 100, 50), price_known=True,
        )


def _run_dirs(tmp_path: Path) -> dict:
    return {
        "workspaces_root": tmp_path / "workspaces",
        "traces_dir": tmp_path / "traces",
        "proof_root": tmp_path / "proof",
    }


def test_run_builds_briefs_after_scout(monkeypatch, tmp_path) -> None:
    from issa_as.run import run as autonomous_run

    monkeypatch.chdir(tmp_path)
    dirs = _run_dirs(tmp_path)
    stub = RunStub()
    outcome = autonomous_run(
        languages="python", limit=10, max_usd=0.50, run_id="run-brief-1",
        client=FakeGitHub(), provider=stub,
        proof_dir=dirs["proof_root"], **dirs,
    )
    assert outcome.verdict == "ABSTAIN"  # not a real bug: nothing to solve
    doc = json.loads(
        (tmp_path / "scout" / "run-brief-1.briefs.json").read_text(encoding="utf-8"))
    assert set(doc["briefs"]) == {ISSUE_URL}
    assert doc["briefs"][ISSUE_URL]["source_language"] == "zh"
    events = trace_log.load("run-brief-1", dirs["traces_dir"])
    briefed = [e for e in events if e.type == "candidates_briefed"]
    assert len(briefed) == 1
    assert briefed[0].count == 1
    stages = [c["stage"] for c in stub.calls]
    assert stages.index("brief") < stages.index("triage")
    assert stub.calls[0]["effort"] == "low"


def test_run_dry_run_skips_briefs(monkeypatch, tmp_path, capsys) -> None:
    from issa_as.run import run as autonomous_run

    monkeypatch.chdir(tmp_path)
    dirs = _run_dirs(tmp_path)

    class Exploding:
        name = "boom"
        model = "boom-model"

        def complete(self, **kwargs):
            raise AssertionError("paid provider must not be called")

    outcome = autonomous_run(
        languages="python", limit=10, max_usd=0.50, dry_run=True,
        run_id="run-dry", client=FakeGitHub(), provider=Exploding(),
        proof_dir=dirs["proof_root"], **dirs,
    )
    assert outcome.verdict == "DRY_RUN"
    out = capsys.readouterr().out
    assert "brief" in out and "est_in_tokens=" in out
    assert not (tmp_path / "scout" / "run-dry.briefs.json").exists()
    events = trace_log.load("run-dry", dirs["traces_dir"])
    assert [e for e in events if e.type == "candidates_briefed"] == []


def test_run_brief_failure_never_blocks_loop(monkeypatch, tmp_path) -> None:
    from issa_as.run import run as autonomous_run

    monkeypatch.chdir(tmp_path)
    dirs = _run_dirs(tmp_path)
    outcome = autonomous_run(
        languages="python", limit=10, max_usd=0.50, run_id="run-brief-fail",
        client=FakeGitHub(), provider=RunStub(fail_brief=True),
        proof_dir=dirs["proof_root"], **dirs,
    )
    assert outcome.verdict == "ABSTAIN"  # triage/select still ran
    doc = json.loads(
        (tmp_path / "scout" / "run-brief-fail.briefs.json").read_text(
            encoding="utf-8"))
    assert doc["briefs"] == {}
    assert doc["error"] != ""
    events = trace_log.load("run-brief-fail", dirs["traces_dir"])
    briefed = [e for e in events if e.type == "candidates_briefed"]
    assert len(briefed) == 1
    assert briefed[0].count == 0
    assert [e for e in events if e.type == "triage_scored"] != []


# -- panel API: estimate/action brief, briefs + live endpoints --------------


def panel_call(server, method, path, body=None):
    """Run one request through the real PanelServer handlers (no sockets)."""
    from issa_as import serve as serve_mod

    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    msg = Message()
    msg["Host"] = "127.0.0.1:8780"
    if payload:
        msg["Content-Type"] = "application/json"
    msg["Content-Length"] = str(len(payload))
    handler = serve_mod.PanelHandler.__new__(serve_mod.PanelHandler)
    handler.server = server
    handler.command = method
    handler.path = path
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.headers = msg
    handler.rfile = io.BytesIO(payload)
    handler.wfile = io.BytesIO()
    getattr(handler, f"do_{method}")()
    head, _, data = handler.wfile.getvalue().partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    try:
        doc = json.loads(data.decode("utf-8")) if data else None
    except ValueError:
        doc = None
    return status, doc


def wait_panel_job(server, job_id: str, timeout_s: float = 15.0) -> dict:
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, job = panel_call(server, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.fixture()
def brief_panel(tmp_path):
    from issa_as import serve as serve_mod

    root = tmp_path / "panelroot"
    (root / "web").mkdir(parents=True)
    server = serve_mod.PanelServer(("127.0.0.1", 0), root, bind_and_activate=False)
    calls: list[tuple[str, dict]] = []

    def fast_stub(srv, params):
        calls.append(("fast", dict(params)))
        return {"ok": True, "run_id": params.get("run_id", "r-stub")}

    server.runners.update({kind: fast_stub for kind in
                           ("scout", "triage", "solve", "recover", "reverify",
                            "run", "brief")})
    yield {"server": server, "root": root, "calls": calls}
    server.server_close()


def _write_panel_scout_report(root: Path, run_id: str = "s1") -> None:
    scout_dir = root / "scout"
    scout_dir.mkdir(parents=True, exist_ok=True)
    (scout_dir / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id,
        "rate_limited": False,
        "candidates": [{
            "issue_url": ISSUE_URL, "repo": "o/r", "number": 1,
            "title": "Crash on empty input", "labels": ["bug"],
            "security_class": None, "runner": "python", "stars": 120,
            "has_snippet": True, "signals": {"has_repro_steps": 1.0},
            "score": 0.7, "body_excerpt": "Steps to reproduce: run it.",
        }],
    }), encoding="utf-8")


def _write_panel_briefs(root: Path, run_id: str = "s1") -> None:
    (root / "scout").mkdir(parents=True, exist_ok=True)
    (root / "scout" / f"{run_id}.briefs.json").write_text(json.dumps({
        "scout_run_id": run_id, "run_id": f"brief-{run_id}",
        "model": "m", "provider": "p", "n_candidates": 1, "error": "",
        "briefs": {ISSUE_URL: brief_entry()},
    }), encoding="utf-8")


def test_estimate_brief_offline(brief_panel) -> None:
    from issa_as import serve as serve_mod

    root = brief_panel["root"]
    _write_panel_scout_report(root)
    estimate = serve_mod.estimate_brief(root, "s1")
    assert estimate["scout_run_id"] == "s1"
    assert estimate["n_candidates"] == 1
    assert estimate["brief"]["est_in_tokens"] > 0
    assert estimate["brief"]["est_out_tokens"] > 0
    with pytest.raises(FileNotFoundError, match="no such scout report"):
        serve_mod.estimate_brief(root, "nope")
    (root / "scout" / "empty.json").write_text(
        json.dumps({"run_id": "empty", "candidates": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="no candidates"):
        serve_mod.estimate_brief(root, "empty")


def test_brief_action_needs_confirm_token(brief_panel) -> None:
    server = brief_panel["server"]
    _write_panel_scout_report(brief_panel["root"])
    status, doc = panel_call(
        server, "POST", "/api/actions/brief", body={"scout_run_id": "s1"})
    assert status == 403
    assert "confirm token" in doc["error"]


def test_estimate_brief_then_confirm_action(brief_panel) -> None:
    server = brief_panel["server"]
    _write_panel_scout_report(brief_panel["root"])
    status, estimate = panel_call(
        server, "POST", "/api/estimate/brief", body={"scout_run_id": "s1"})
    assert status == 200
    assert estimate["n_candidates"] == 1
    token = estimate["confirm_token"]
    status, started = panel_call(
        server, "POST", "/api/actions/brief",
        body={"scout_run_id": "s1", "confirm_token": token})
    assert status == 200
    assert started["kind"] == "brief"
    job = wait_panel_job(server, started["job_id"])
    assert job["status"] == "done"
    assert job["run_id"]  # job dump carries the trace run id
    status, missing = panel_call(
        server, "POST", "/api/estimate/brief", body={"scout_run_id": "nope"})
    assert status == 404


def test_scout_detail_embeds_briefs_when_present(brief_panel) -> None:
    server, root = brief_panel["server"], brief_panel["root"]
    _write_panel_scout_report(root)
    _write_panel_briefs(root)
    status, doc = panel_call(server, "GET", "/api/scout/s1")
    assert status == 200
    assert doc["briefs"][ISSUE_URL]["title"]["en"] == "Crash on empty input"


def test_scout_detail_works_without_briefs(brief_panel) -> None:
    server, root = brief_panel["server"], brief_panel["root"]
    _write_panel_scout_report(root)
    status, doc = panel_call(server, "GET", "/api/scout/s1")
    assert status == 200
    assert doc["briefs"] == {}
    assert len(doc["candidates"]) == 1


def test_briefs_endpoint(brief_panel) -> None:
    server, root = brief_panel["server"], brief_panel["root"]
    _write_panel_scout_report(root)
    assert panel_call(server, "GET", "/api/briefs/s1")[0] == 404
    _write_panel_briefs(root)
    status, doc = panel_call(server, "GET", "/api/briefs/s1")
    assert status == 200
    assert doc["scout_run_id"] == "s1"
    assert doc["briefs"][ISSUE_URL]["source_language"] == "zh"


def test_triage_detail_embeds_briefs(brief_panel) -> None:
    from issa_as import serve as serve_mod

    root = brief_panel["root"]
    _write_panel_scout_report(root)
    _write_panel_briefs(root)
    triage_dir = root / "triage"
    triage_dir.mkdir(parents=True, exist_ok=True)
    (triage_dir / "t1.json").write_text(json.dumps({
        "run_id": "t1", "scout_run_id": "s1", "scored": [],
        "critic": [], "selection": {},
    }), encoding="utf-8")
    detail = serve_mod.triage_detail(root, "t1")
    assert detail is not None
    assert detail["briefs"][ISSUE_URL]["title"]["es"] == "Fallo con entrada vacía"


def _write_live_trace(root: Path, run_id: str, *, verdict: str = "") -> None:
    traces_dir = root / "traces"
    trace_log.append(run_id, {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append(run_id, {
        "type": "issue_discovered", "stage": "scout",
        "issue_url": ISSUE_URL, "repo": "o/r"}, traces_dir)
    trace_log.append(run_id, {
        "type": "candidates_briefed", "stage": "brief",
        "model": "m", "provider": "p", "count": 1}, traces_dir)
    trace_log.append(run_id, {
        "type": "triage_scored", "stage": "triage", "issue_id": ISSUE_URL,
        "solvability": 0.8}, traces_dir)
    trace_log.append(run_id, {
        "type": "selected", "stage": "select", "issue_id": ISSUE_URL,
        "model": "muse-spark-1.3", "effort": "high"}, traces_dir)
    trace_log.append(run_id, {
        "type": "fix_attempt", "stage": "fixer", "issue_id": ISSUE_URL}, traces_dir)
    if verdict:
        trace_log.append(run_id, {
            "type": "verify_verdict", "stage": "verifier",
            "issue_id": ISSUE_URL, "verdict": verdict}, traces_dir)


def test_live_status_returns_stage_and_selected_issue(brief_panel) -> None:
    from issa_as import serve as serve_mod

    server, root = brief_panel["server"], brief_panel["root"]
    _write_live_trace(root, "r-live-1", verdict="VERIFIED")
    _write_panel_briefs(root)
    live = serve_mod.live_status(root, "r-live-1")
    assert live is not None
    assert live["stage"] == "verifying"
    assert live["issue_url"] == ISSUE_URL
    assert live["model"] == "muse-spark-1.3"
    assert live["effort"] == "high"
    assert live["brief"]["title"]["pt"] == "Falha com entrada vazia"
    assert live["verdict"] == "VERIFIED"
    status, doc = panel_call(server, "GET", "/api/live/r-live-1")
    assert status == 200
    assert doc["stage"] == "verifying"
    assert doc["issue_url"] == ISSUE_URL


def test_live_status_early_run_has_no_selection(brief_panel) -> None:
    from issa_as import serve as serve_mod

    root = brief_panel["root"]
    traces_dir = root / "traces"
    trace_log.append("r-early-1", {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append("r-early-1", {
        "type": "issue_discovered", "stage": "scout",
        "issue_url": ISSUE_URL}, traces_dir)
    live = serve_mod.live_status(root, "r-early-1")
    assert live is not None
    assert live["stage"] == "scouting"
    assert live["issue_url"] == ""
    assert live["brief"] is None
    assert live["verdict"] == ""


def test_live_status_unknown_run(brief_panel) -> None:
    server = brief_panel["server"]
    assert panel_call(server, "GET", "/api/live/no-such-run")[0] == 404
    assert panel_call(server, "GET", "/api/live/bad!id")[0] == 400


# -- web panel: language switch, briefs, live card ----------------------------


def _web(name: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "web" / name).read_text(encoding="utf-8")


def test_panel_has_language_switch() -> None:
    html = _web("index.html")
    for lang in ("en", "es", "pt"):
        assert f'data-lang="{lang}"' in html
    assert "localStorage" not in html  # storage access lives in app.js
    js = _web("app.js")
    assert 'localStorage.getItem("issa-lang")' in js
    assert "try" in js  # wrapped in try/catch


def test_panel_renders_briefs_with_text_content_only() -> None:
    js = _web("app.js")
    assert "innerHTML" not in js
    assert "renderBrief" in js
    assert "Original (" in js
    assert "now-card" in js
    assert "}, 3000);" in js  # live feed polls every 3 s while a job runs


def test_panel_marks_missing_translation_with_english_note() -> None:
    js = _web("app.js")
    assert "innerHTML" not in js  # the note still renders via textContent only
    assert "(English — translation unavailable)" in js


def test_panel_has_brief_action_and_live_card() -> None:
    html = _web("index.html")
    assert 'id="now-card"' in html
    assert 'id="form-brief"' in html
    assert "Explain candidates" in _web("app.js")
    assert '"brief"' in _web("app.js")
    css = _web("styles.css")
    assert ".langswitch" in css
    assert ".brief" in css
    assert "#now-card" in css


def test_default_routing_matches_triage(monkeypatch) -> None:
    monkeypatch.setenv("SPARK_OPEN_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SPARK_OPEN_API_KEY", "test-key")
    assert briefs_mod.default_provider().name == "custom"
    monkeypatch.delenv("SPARK_OPEN_BASE_URL", raising=False)
    monkeypatch.delenv("SPARK_OPEN_API_KEY", raising=False)
    provider = briefs_mod.default_provider()
    assert provider.name == "meta-api"
    assert provider.model == "muse-spark-1.3-contributor"


# -- Prompt 29.2: the brief-building path stays out of the repo scout/ --------


def test_run_brief_path_leaves_repo_scout_untouched(tmp_path) -> None:
    """The autonomous run writes briefs to ``scout_dir``, never the repo root."""
    from issa_as.run import run as autonomous_run

    repo_scout = Path(__file__).resolve().parents[1] / "scout"
    before = sorted(p.name for p in repo_scout.iterdir()) if repo_scout.is_dir() else []
    scout_dir = tmp_path / "scout"
    outcome = autonomous_run(
        languages="python",
        limit=10,
        max_usd=0.50,
        run_id="run-repo-scout-guard",
        client=FakeGitHub(),
        provider=RunStub(),
        workspaces_root=tmp_path / "workspaces",
        traces_dir=tmp_path / "traces",
        proof_root=tmp_path / "proof",
        proof_dir=tmp_path / "proof",
        scout_dir=scout_dir,
    )
    assert outcome.verdict == "ABSTAIN"
    assert (scout_dir / "run-repo-scout-guard.briefs.json").is_file()
    after = sorted(p.name for p in repo_scout.iterdir()) if repo_scout.is_dir() else []
    assert after == before

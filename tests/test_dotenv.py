"""Offline tests for the stdlib .env loader."""

import os

from issa_as.dotenv import load_dotenv, parse_dotenv_text


def test_parse_handles_comments_quotes_and_export() -> None:
    text = (
        "# a comment\n"
        "MODEL_API_KEY=abc123\n"
        "EMPTY=\n"
        "QUOTED=\"hello world\"\n"
        "SINGLE='it works'\n"
        "export SPACED = spaced value \n"
        "NOT_A_PAIR\n"
    )
    parsed = parse_dotenv_text(text)
    assert parsed == {
        "MODEL_API_KEY": "abc123",
        "EMPTY": "",
        "QUOTED": "hello world",
        "SINGLE": "it works",
        "SPACED": "spaced value",
    }


def test_load_sets_missing_keys(tmp_path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text("MODEL_API_KEY=file-key\nNEW_KEY=new\n", encoding="utf-8")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("NEW_KEY", raising=False)
    loaded = load_dotenv(path)
    assert loaded == {"MODEL_API_KEY": "file-key", "NEW_KEY": "new"}
    assert os.environ["MODEL_API_KEY"] == "file-key"


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text("MODEL_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("MODEL_API_KEY", "real-key")
    load_dotenv(path)
    assert os.environ["MODEL_API_KEY"] == "real-key"


def test_missing_file_is_not_an_error(tmp_path) -> None:
    assert load_dotenv(tmp_path / "does-not-exist") == {}


def test_loader_prints_nothing(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / ".env"
    path.write_text("MODEL_API_KEY=super-secret-value\n", encoding="utf-8")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    load_dotenv(path)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    assert "super-secret-value" not in out.out

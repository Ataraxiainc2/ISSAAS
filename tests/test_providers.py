"""Tests for providers.py. No real API calls: FakeProvider, FailingProvider, MockTransport."""

import json

import httpx
import pytest

from issa_as import traces
from issa_as.budget import IssueCaps, Ledger
from issa_as.providers import (
    FakeProvider,
    FailingProvider,
    ProviderError,
    complete_with_fallback,
    meta_provider,
    spark_open_provider,
    triage_provider,
)

CAPS = IssueCaps(max_steps=10, max_seconds=600.0, max_usd=0.60)


def _chat_payload(text: str, model: str, in_tokens: int = 100, out_tokens: int = 50) -> dict:
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": in_tokens, "completion_tokens": out_tokens},
    }


def test_fake_provider_charges_ledger_and_emits_model_call(tmp_path) -> None:
    provider = FakeProvider(text="solvable", in_tokens=1000, out_tokens=500)
    ledger = Ledger(issue_id="i1")
    completion = provider.complete(
        stage="triage",
        prompt="score this",
        ledger=ledger,
        caps=CAPS,
        run_id="r-fake",
        issue_id="i1",
        traces_dir=tmp_path,
    )
    assert completion.text == "solvable"
    assert completion.provider == "fake"
    assert ledger.calls == 1
    assert ledger.usd_spent > 0
    (event,) = traces.load("r-fake", tmp_path)
    assert event.type == "model_call"
    assert (event.in_tokens, event.out_tokens) == (1000, 500)
    assert event.cost_usd == pytest.approx(ledger.usd_spent)


def test_fake_provider_cap_block_propagates(tmp_path) -> None:
    provider = FakeProvider(in_tokens=10_000_000, out_tokens=10_000_000, model="muse-spark-1.3")
    ledger = Ledger(issue_id="i2")
    tiny = IssueCaps(max_steps=10, max_seconds=600.0, max_usd=0.01)
    with pytest.raises(Exception, match="usd"):
        provider.complete(
            stage="triage", prompt="x", ledger=ledger, caps=tiny, run_id="r-fb", traces_dir=tmp_path
        )
    assert ledger.calls == 0


def test_fallback_on_provider_error(tmp_path, caplog) -> None:
    primary = FailingProvider(name="spark-open-hosted")
    fallback = FakeProvider(text="fallback answer", name="meta-api")
    with caplog.at_level("WARNING"):
        completion = complete_with_fallback(
            primary, fallback, stage="critic", prompt="rescore", run_id="r-fw", traces_dir=tmp_path
        )
    assert completion.text == "fallback answer"
    assert "falling back" in caplog.text
    (event,) = traces.load("r-fw", tmp_path)
    assert event.type == "model_call"
    assert event.provider == "meta-api"  # records who actually ran


def test_triage_provider_prefers_spark_open(monkeypatch) -> None:
    monkeypatch.setenv("SPARK_OPEN_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SPARK_OPEN_API_KEY", "test-key")
    provider = triage_provider()
    assert provider.name == "spark-open-hosted"
    assert provider.model == "meta-models/muse-spark-open-1026"


def test_triage_provider_falls_back_to_spark_13(monkeypatch) -> None:
    monkeypatch.delenv("SPARK_OPEN_BASE_URL", raising=False)
    monkeypatch.delenv("SPARK_OPEN_API_KEY", raising=False)
    provider = triage_provider()
    assert provider.name == "meta-api"
    assert provider.model == "muse-spark-1.3"


def test_spark_open_provider_none_without_creds(monkeypatch) -> None:
    monkeypatch.delenv("SPARK_OPEN_BASE_URL", raising=False)
    monkeypatch.delenv("SPARK_OPEN_API_KEY", raising=False)
    assert spark_open_provider() is None


def test_http_provider_parses_openai_shape(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["model"] == "muse-spark-1.3"
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        return httpx.Response(200, json=_chat_payload("hi", "muse-spark-1.3", 200, 40))

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.meta.ai/v1"
    )
    provider = meta_provider(client=client)
    ledger = Ledger(issue_id="i3")
    completion = provider.complete(
        stage="triage",
        prompt="hello",
        ledger=ledger,
        caps=CAPS,
        run_id="r-http",
        traces_dir=tmp_path,
    )
    assert completion.text == "hi"
    assert (completion.in_tokens, completion.out_tokens) == (200, 40)
    assert completion.cost_usd == pytest.approx((200 * 1.25 + 40 * 4.25) / 1_000_000)
    assert ledger.calls == 1
    (event,) = traces.load("r-http", tmp_path)
    assert event.type == "model_call"
    assert event.provider == "meta-api"


def test_http_provider_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "overloaded"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError):
        meta_provider(client=client).complete(stage="triage", prompt="hello")

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
    monkeypatch.delenv("SPARK_OPEN_MODEL", raising=False)
    provider = triage_provider()
    assert provider.name == "custom"
    assert provider.model == "meta-models/muse-spark-open"


def test_spark_open_model_id_from_env(monkeypatch) -> None:
    from issa_as.budget import spark_open_model

    monkeypatch.setenv("SPARK_OPEN_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SPARK_OPEN_API_KEY", "test-key")
    monkeypatch.setenv("SPARK_OPEN_MODEL", "meta-models/muse-spark-open-1026")
    assert spark_open_model() == "meta-models/muse-spark-open-1026"
    assert spark_open_provider().model == "meta-models/muse-spark-open-1026"
    monkeypatch.delenv("SPARK_OPEN_MODEL")
    assert spark_open_model() == "meta-models/muse-spark-open"


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
        return httpx.Response(400, json={"error": "bad request"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError):
        meta_provider(client=client).complete(stage="triage", prompt="hello")


def test_http_provider_retries_429_then_succeeds() -> None:
    sleeps: list[float] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=_chat_payload("ok", "muse-spark-1.3", 50, 10))

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.meta.ai/v1"
    )
    provider = meta_provider(client=client)
    provider.backoff_base_s = 0.0
    provider.sleep_fn = sleeps.append
    completion = provider.complete(stage="triage", prompt="hello")
    assert completion.text == "ok"
    assert calls["n"] == 2
    assert sleeps == [0.0]  # backoff before the retry


def test_http_provider_honors_retry_after() -> None:
    sleeps: list[float] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, json={"error": "slow down"}, headers={"Retry-After": "7"}
            )
        return httpx.Response(200, json=_chat_payload("ok", "muse-spark-1.3", 50, 10))

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.meta.ai/v1"
    )
    provider = meta_provider(client=client)
    provider.sleep_fn = sleeps.append
    provider.complete(stage="triage", prompt="hello")
    assert sleeps == [7.0]


def test_http_provider_gives_up_after_three_retries() -> None:
    sleeps: list[float] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "overloaded"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.meta.ai/v1"
    )
    provider = meta_provider(client=client)
    provider.backoff_base_s = 0.0
    provider.sleep_fn = sleeps.append
    with pytest.raises(ProviderError, match="after 3 retries"):
        provider.complete(stage="triage", prompt="hello")
    assert calls["n"] == 4  # initial + 3 retries, never in parallel
    assert sleeps == [0.0, 0.0, 0.0]


def test_http_provider_does_not_retry_client_errors() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.meta.ai/v1"
    )
    with pytest.raises(ProviderError):
        meta_provider(client=client).complete(stage="triage", prompt="hello")
    assert calls["n"] == 1


def test_request_sends_explicit_reasoning_effort_and_max_tokens() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_chat_payload("ok", "muse-spark-1.3", 10, 5))

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.meta.ai/v1"
    )
    meta_provider(client=client).complete(
        stage="triage", prompt="hello", effort="low", max_tokens=5000
    )
    assert bodies[0]["reasoning_effort"] == "low"  # never omitted (xhigh default)
    assert bodies[0]["max_tokens"] == 5000
    meta_provider(client=client).complete(stage="triage", prompt="hello")
    assert bodies[1]["reasoning_effort"] == "low"
    assert bodies[1]["max_tokens"] == 512  # effort fallback when unset


def test_reasoning_effort_maps_unknown_effort_to_low() -> None:
    from issa_as.providers import reasoning_effort_for

    assert reasoning_effort_for("high") == "high"
    assert reasoning_effort_for("xhigh") == "xhigh"
    assert reasoning_effort_for("minimal") == "low"


def test_max_tokens_for_batch_sizes_and_caps() -> None:
    from issa_as.providers import MAX_OUTPUT_TOKENS, max_tokens_for_batch

    assert max_tokens_for_batch(1) == 600 + 2000
    assert max_tokens_for_batch(30) == MAX_OUTPUT_TOKENS  # 600*30+2000 capped
    assert max_tokens_for_batch(0) == 600 + 2000


def test_reasoning_fields_recorded_on_completion_and_trace(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-abc123",
                "model": "meta-models/muse-spark-open",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"issues": []}',
                            "reasoning_content": "thinking..." * 1000,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 200,
                    "completion_tokens": 1500,
                    "completion_tokens_details": {"reasoning_tokens": 1200},
                },
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://example.invalid/v1"
    )
    provider = meta_provider(client=client)
    completion = provider.complete(
        stage="triage", prompt="hi", run_id="r-reason", traces_dir=tmp_path
    )
    assert completion.reasoning_tokens == 1200
    assert completion.request_id == "chatcmpl-abc123"
    assert completion.reasoning_excerpt == ("thinking..." * 1000)[:1500]
    assert completion.truncated is False
    (event,) = traces.load("r-reason", tmp_path)
    assert event.type == "model_call"
    assert event.reasoning_tokens == 1200
    assert event.request_id == "chatcmpl-abc123"
    assert event.reasoning_excerpt == completion.reasoning_excerpt


def test_spark_open_defaults_to_baseten(monkeypatch) -> None:
    from issa_as.providers import BASETEN_BASE_URL

    assert BASETEN_BASE_URL == "https://inference.baseten.co/v1"
    monkeypatch.setenv("SPARK_OPEN_API_KEY", "test-key")
    monkeypatch.delenv("SPARK_OPEN_BASE_URL", raising=False)
    monkeypatch.delenv("SPARK_OPEN_MODEL", raising=False)
    provider = spark_open_provider()
    assert provider is not None
    assert provider.base_url == BASETEN_BASE_URL
    assert provider.name == "baseten"
    assert provider.model == "meta-models/muse-spark-open"


def test_spark_open_explicit_base_url_is_custom(monkeypatch) -> None:
    monkeypatch.setenv("SPARK_OPEN_API_KEY", "test-key")
    monkeypatch.setenv("SPARK_OPEN_BASE_URL", "https://example.invalid/v1")
    provider = spark_open_provider()
    assert provider is not None
    assert provider.base_url == "https://example.invalid/v1"
    assert provider.name == "custom"


def test_spark_open_none_without_api_key_even_with_base_url(monkeypatch) -> None:
    monkeypatch.setenv("SPARK_OPEN_BASE_URL", "https://example.invalid/v1")
    monkeypatch.delenv("SPARK_OPEN_API_KEY", raising=False)
    assert spark_open_provider() is None


def test_spark_open_trace_event_records_provider_name(monkeypatch, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_chat_payload("ok", "meta-models/muse-spark-open", 10, 5)
        )

    monkeypatch.setenv("SPARK_OPEN_API_KEY", "test-key")
    monkeypatch.delenv("SPARK_OPEN_BASE_URL", raising=False)
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://inference.baseten.co/v1",
    )
    completion = spark_open_provider(client=client).complete(
        stage="triage", prompt="hi", run_id="r-baseten", traces_dir=tmp_path
    )
    assert completion.provider == "baseten"
    (event,) = traces.load("r-baseten", tmp_path)
    assert event.type == "model_call"
    assert event.provider == "baseten"

    monkeypatch.setenv("SPARK_OPEN_BASE_URL", "https://example.invalid/v1")
    custom_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://example.invalid/v1",
    )
    custom_completion = spark_open_provider(client=custom_client).complete(
        stage="triage", prompt="hi", run_id="r-custom", traces_dir=tmp_path
    )
    assert custom_completion.provider == "custom"
    (custom_event,) = traces.load("r-custom", tmp_path)
    assert custom_event.type == "model_call"
    assert custom_event.provider == "custom"


def test_finish_reason_length_marks_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = _chat_payload("{\"issues\": [", "m1", 10, 5000)
        payload["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=payload)

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.meta.ai/v1"
    )
    completion = meta_provider(client=client).complete(stage="triage", prompt="hi")
    assert completion.truncated is True
    assert completion.finish_reason == "length"

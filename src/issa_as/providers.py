"""Model routing: Meta Model API with optional hosted Spark Open provider.

Primary triage/critic route is Spark Open when its credentials exist, with
automatic logged fallback to Spark 1.3. Every call emits a ``model_call``
event with token counts, reasoning tokens, the provider request id, a
reasoning excerpt and USD cost. Spark Open requests always set an explicit
``reasoning_effort`` (the provider defaults to ``xhigh``) and a ``max_tokens``
budget sized to the batch. Deterministic budgeting lives in
``budget.py``; this module only proposes via HTTP.

Meta API calls retry 429/5xx with exponential backoff (at most 3 retries,
sequential — requests are never run in parallel).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from issa_as import traces as trace_log
from issa_as.budget import (
    SPARK_CONTRIBUTOR_MODEL,
    SPARK_MODEL,
    SPARK_OPEN_MODEL,
    IssueCaps,
    Ledger,
    TokenUsage,
    check_and_charge,
    cost_usd,
    price_known,
    spark_open_model,
)

logger = logging.getLogger(__name__)

# Meta API retry policy: at most this many retries on 429/5xx, sequential.
MAX_RETRIES = 3
BACKOFF_BASE_S = 1.0

META_API_BASE = "https://api.meta.ai/v1"
MODEL_API_KEY_ENV = "MODEL_API_KEY"
SPARK_OPEN_BASE_URL_ENV = "SPARK_OPEN_BASE_URL"
SPARK_OPEN_API_KEY_ENV = "SPARK_OPEN_API_KEY"

# Default hosted Spark Open endpoint (Baseten) when only the API key is set.
BASETEN_BASE_URL = "https://inference.baseten.co/v1"

# Fallback output budget per effort level for the pre-call cap estimate.
EFFORT_MAX_TOKENS = {"low": 512, "medium": 2048, "high": 8192}

# Batched JSON sizing: the 30-candidate triage answer was truncated at 512
# output tokens, so triage/critic size ``max_tokens`` per batch instead.
TOKENS_PER_CANDIDATE = 600
REASONING_RESERVE_TOKENS = 2000
MAX_OUTPUT_TOKENS = 16000

# First N chars of ``message.reasoning_content`` kept on the trace event.
REASONING_EXCERPT_CHARS = 1500

# Spark Open accepts low|medium|high|xhigh and defaults to xhigh (expensive)
# when omitted — always send it explicitly (cheap triage/critic use "low").
REASONING_EFFORTS = ("low", "medium", "high", "xhigh")


def reasoning_effort_for(effort: str) -> str:
    """Map a caller effort level onto an explicit ``reasoning_effort`` value."""
    if effort in REASONING_EFFORTS:
        return effort
    return "low"


def max_tokens_for_batch(n_candidates: int) -> int:
    """Output budget for a batched JSON call (reasoning + answer, capped)."""
    return min(
        TOKENS_PER_CANDIDATE * max(n_candidates, 1) + REASONING_RESERVE_TOKENS,
        MAX_OUTPUT_TOKENS,
    )


@dataclass
class Completion:
    """Result of one chat completion."""

    text: str
    model: str
    provider: str
    in_tokens: int = 0
    out_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None
    price_known: bool = True
    # Spark Open reasoning: ``message.reasoning_content`` excerpt plus the
    # reasoning token count (billed inside ``completion_tokens``).
    reasoning_tokens: int = 0
    reasoning_excerpt: str = ""
    # Provider's request id (OpenAI-style top-level ``id``) when returned.
    request_id: str = ""
    # Raw ``finish_reason``; ``truncated`` is True when it is ``"length"``.
    finish_reason: str = ""
    truncated: bool = False


@dataclass
class ProviderError(Exception):
    """Transport or API failure from a model provider."""

    provider: str
    message: str

    def __str__(self) -> str:
        return f"{self.provider}: {self.message}"


class ChatProvider(Protocol):
    """Anything that can complete a chat prompt and account for it."""

    name: str
    model: str

    def complete(
        self,
        *,
        stage: str,
        prompt: str,
        effort: str = "low",
        run_id: str | None = None,
        issue_id: str | None = None,
        ledger: Ledger | None = None,
        caps: IssueCaps | None = None,
        traces_dir: Path | None = None,
        max_tokens: int | None = None,
    ) -> Completion: ...


def _estimate_usage(model: str, prompt: str, effort: str) -> TokenUsage:
    return TokenUsage(
        model=model,
        in_tokens=max(len(prompt) // 4, 1),
        out_tokens=EFFORT_MAX_TOKENS.get(effort, 512),
    )


def _emit_model_call(
    run_id: str | None,
    stage: str,
    issue_id: str | None,
    completion: Completion,
    traces_dir: Path | None,
) -> None:
    if run_id is None:
        return
    trace_log.append(
        run_id,
        trace_log.ModelCall(
            run_id=run_id,
            stage=stage,
            issue_id=issue_id,
            model=completion.model,
            provider=completion.provider,
            in_tokens=completion.in_tokens,
            out_tokens=completion.out_tokens,
            cached_tokens=completion.cached_tokens,
            cost_usd=completion.cost_usd,
            price_known=completion.price_known,
            reasoning_tokens=completion.reasoning_tokens,
            request_id=completion.request_id,
            reasoning_excerpt=completion.reasoning_excerpt,
        ),
        traces_dir,
    )


@dataclass
class HttpChatProvider:
    """OpenAI-compatible chat client (Meta Model API or a hosted provider)."""

    name: str
    model: str
    base_url: str
    api_key: str
    timeout: float = 120.0
    client: httpx.Client | None = field(default=None, repr=False)
    max_retries: int = MAX_RETRIES
    backoff_base_s: float = BACKOFF_BASE_S
    sleep_fn: Callable[[float], None] = field(default_factory=lambda: time.sleep)

    def _client(self) -> httpx.Client:
        if self.client is not None:
            return self.client
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        """Backoff for one retry: Retry-After wins, else exponential."""
        if response is not None:
            raw = response.headers.get("retry-after")
            if raw is not None:
                try:
                    return max(0.0, float(raw))
                except (TypeError, ValueError):
                    pass
        return self.backoff_base_s * (2**attempt)

    def _post_with_retry(self, body: dict[str, Any]) -> httpx.Response:
        """POST once, retrying 429/5xx sequentially with backoff.

        At most ``max_retries`` retries, one request at a time — requests
        are never run in parallel. Other HTTP errors and transport
        failures raise immediately.
        """
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client().post("/chat/completions", json=body)
            except httpx.HTTPError as exc:
                raise ProviderError(self.name, str(exc)) from exc
            status = response.status_code
            if status == 429 or 500 <= status < 600:
                if attempt >= self.max_retries:
                    raise ProviderError(
                        self.name, f"http {status} after {self.max_retries} retries"
                    )
                self.sleep_fn(self._retry_delay(attempt, response))
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(self.name, str(exc)) from exc
            return response
        raise ProviderError(self.name, "retry loop exhausted")  # pragma: no cover

    def complete(
        self,
        *,
        stage: str,
        prompt: str,
        effort: str = "low",
        run_id: str | None = None,
        issue_id: str | None = None,
        ledger: Ledger | None = None,
        caps: IssueCaps | None = None,
        traces_dir: Path | None = None,
        max_tokens: int | None = None,
    ) -> Completion:
        if ledger is not None and caps is not None:
            check_and_charge(
                ledger,
                _estimate_usage(self.model, prompt, effort),
                caps,
                run_id=run_id,
                stage=stage,
                issue_id=issue_id,
                traces_dir=traces_dir,
                dry_run=True,
            )
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            # Spark Open defaults to xhigh (expensive) when omitted.
            "reasoning_effort": reasoning_effort_for(effort),
            # Reasoning tokens count toward completion_tokens, so this must
            # leave room for reasoning plus the JSON answer.
            "max_tokens": max_tokens
            if max_tokens is not None
            else EFFORT_MAX_TOKENS.get(effort, 512),
        }
        try:
            data = self._post_with_retry(body).json()
        except ProviderError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(self.name, f"unexpected response shape: {exc}") from exc
        completion = self._parse_response(data, stage)
        if ledger is not None and caps is not None:
            check_and_charge(
                ledger,
                TokenUsage(
                    model=self.model,
                    in_tokens=completion.in_tokens,
                    out_tokens=completion.out_tokens,
                    cached_tokens=completion.cached_tokens,
                ),
                caps,
                run_id=run_id,
                stage=stage,
                issue_id=issue_id,
                traces_dir=traces_dir,
            )
        else:
            completion.cost_usd = cost_usd(
                self.model,
                completion.in_tokens,
                completion.out_tokens,
                completion.cached_tokens,
            )
        _emit_model_call(run_id, stage, issue_id, completion, traces_dir)
        return completion

    def _parse_response(self, data: dict[str, Any], stage: str) -> Completion:
        try:
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError(self.name, f"unexpected response shape: {data!r}") from exc
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens", 0) or 0
        in_tokens = usage.get("prompt_tokens", 0) or 0
        out_tokens = usage.get("completion_tokens", 0) or 0
        reasoning_details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = reasoning_details.get("reasoning_tokens", 0) or 0
        reasoning_content = message.get("reasoning_content") or ""
        finish_reason = ""
        try:
            finish_reason = data["choices"][0].get("finish_reason") or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            finish_reason = ""
        return Completion(
            text=text,
            model=data.get("model", self.model),
            provider=self.name,
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            cached_tokens=cached,
            cost_usd=cost_usd(self.model, in_tokens, out_tokens, cached),
            price_known=price_known(self.model),
            reasoning_tokens=reasoning_tokens,
            reasoning_excerpt=str(reasoning_content)[:REASONING_EXCERPT_CHARS],
            request_id=str(data.get("id") or ""),
            finish_reason=str(finish_reason),
            truncated=finish_reason == "length",
        )


@dataclass
class FakeProvider:
    """Deterministic stub for tests. No network, fixed token counts."""

    text: str = "fake response"
    name: str = "fake"
    model: str = SPARK_MODEL
    in_tokens: int = 100
    out_tokens: int = 50
    cached_tokens: int = 0

    def complete(
        self,
        *,
        stage: str,
        prompt: str,
        effort: str = "low",
        run_id: str | None = None,
        issue_id: str | None = None,
        ledger: Ledger | None = None,
        caps: IssueCaps | None = None,
        traces_dir: Path | None = None,
        max_tokens: int | None = None,
    ) -> Completion:
        _ = (prompt, effort, max_tokens)
        usage = TokenUsage(
            model=self.model,
            in_tokens=self.in_tokens,
            out_tokens=self.out_tokens,
            cached_tokens=self.cached_tokens,
        )
        cost: float | None = 0.0
        if ledger is not None and caps is not None:
            cost = check_and_charge(
                ledger,
                usage,
                caps,
                run_id=run_id,
                stage=stage,
                issue_id=issue_id,
                traces_dir=traces_dir,
            )
        else:
            cost = cost_usd(self.model, self.in_tokens, self.out_tokens, self.cached_tokens)
        completion = Completion(
            text=self.text,
            model=self.model,
            provider=self.name,
            in_tokens=self.in_tokens,
            out_tokens=self.out_tokens,
            cached_tokens=self.cached_tokens,
            cost_usd=cost,
            price_known=price_known(self.model),
        )
        _emit_model_call(run_id, stage, issue_id, completion, traces_dir)
        return completion


@dataclass
class FailingProvider:
    """Test helper that always fails, to exercise fallback."""

    name: str = "failing"
    model: str = "broken-model"
    error: str = "boom"

    def complete(self, **kwargs: Any) -> Completion:
        raise ProviderError(self.name, self.error)


def meta_provider(
    model: str = SPARK_MODEL, client: httpx.Client | None = None
) -> HttpChatProvider:
    """Spark 1.3 / contributor client for the Meta Model API. Reads env at call time."""
    return HttpChatProvider(
        name="meta-api",
        model=model,
        base_url=META_API_BASE,
        api_key=os.environ.get(MODEL_API_KEY_ENV, ""),
        client=client,
    )


def spark_open_provider(client: httpx.Client | None = None) -> HttpChatProvider | None:
    """Hosted Spark Open client, or None when its API key is absent.

    When ``SPARK_OPEN_API_KEY`` is set and ``SPARK_OPEN_BASE_URL`` is empty,
    the base URL defaults to :data:`BASETEN_BASE_URL`. The provider name on
    trace events is ``"baseten"`` for that default and ``"custom"`` for an
    explicit base URL. The model id comes from env ``SPARK_OPEN_MODEL``
    (default ``meta-models/muse-spark-open``); the request always sets an
    explicit ``reasoning_effort`` because the provider defaults to ``xhigh``.
    """
    api_key = os.environ.get(SPARK_OPEN_API_KEY_ENV, "")
    if not api_key:
        return None
    base_url = os.environ.get(SPARK_OPEN_BASE_URL_ENV, "").strip()
    name = "custom" if base_url else "baseten"
    return HttpChatProvider(
        name=name,
        model=spark_open_model(),
        base_url=base_url or BASETEN_BASE_URL,
        api_key=api_key,
        client=client,
    )


def triage_provider(client: httpx.Client | None = None) -> HttpChatProvider:
    """Pluggable triage route: Spark Open when configured, else Spark 1.3."""
    open_provider = spark_open_provider(client=client)
    if open_provider is not None:
        return open_provider
    return meta_provider(client=client)


def complete_with_fallback(
    primary: ChatProvider,
    fallback: ChatProvider,
    *,
    stage: str,
    prompt: str,
    effort: str = "low",
    run_id: str | None = None,
    issue_id: str | None = None,
    ledger: Ledger | None = None,
    caps: IssueCaps | None = None,
    traces_dir: Path | None = None,
    max_tokens: int | None = None,
) -> Completion:
    """Run the primary provider; on transport/API failure log and use fallback.

    Cap blocks are never fallen back through: they propagate to the caller.
    The ``model_call`` event always records which provider actually ran.
    """
    try:
        return primary.complete(
            stage=stage,
            prompt=prompt,
            effort=effort,
            run_id=run_id,
            issue_id=issue_id,
            ledger=ledger,
            caps=caps,
            traces_dir=traces_dir,
            max_tokens=max_tokens,
        )
    except ProviderError as exc:
        logger.warning(
            "provider %s failed, falling back to %s: %s", primary.name, fallback.name, exc
        )
        return fallback.complete(
            stage=stage,
            prompt=prompt,
            effort=effort,
            run_id=run_id,
            issue_id=issue_id,
            ledger=ledger,
            caps=caps,
            traces_dir=traces_dir,
            max_tokens=max_tokens,
        )


def complete(
    stage: str,
    prompt: str,
    effort: str = "low",
    *,
    run_id: str | None = None,
    issue_id: str | None = None,
    ledger: Ledger | None = None,
    caps: IssueCaps | None = None,
    traces_dir: Path | None = None,
    max_tokens: int | None = None,
) -> Completion:
    """Triage-route completion: Spark Open when configured, else Spark 1.3 with fallback."""
    return complete_with_fallback(
        triage_provider(),
        meta_provider(),
        stage=stage,
        prompt=prompt,
        effort=effort,
        run_id=run_id,
        issue_id=issue_id,
        ledger=ledger,
        caps=caps,
        traces_dir=traces_dir,
        max_tokens=max_tokens,
    )


__all__ = [
    "BASETEN_BASE_URL",
    "SPARK_CONTRIBUTOR_MODEL",
    "SPARK_MODEL",
    "SPARK_OPEN_MODEL",
    "ChatProvider",
    "Completion",
    "EFFORT_MAX_TOKENS",
    "MAX_OUTPUT_TOKENS",
    "REASONING_EFFORTS",
    "REASONING_EXCERPT_CHARS",
    "REASONING_RESERVE_TOKENS",
    "TOKENS_PER_CANDIDATE",
    "FailingProvider",
    "FakeProvider",
    "HttpChatProvider",
    "ProviderError",
    "complete",
    "complete_with_fallback",
    "max_tokens_for_batch",
    "meta_provider",
    "price_known",
    "reasoning_effort_for",
    "spark_open_provider",
    "triage_provider",
]

"""Model routing: Meta Model API with optional hosted Spark Open provider.

Primary triage/critic route is Spark Open when its credentials exist, with
automatic logged fallback to Spark 1.3. Every call emits a ``model_call``
event with token counts and USD cost. Deterministic budgeting lives in
``budget.py``; this module only proposes via HTTP.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

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
)

logger = logging.getLogger(__name__)

META_API_BASE = "https://api.meta.ai/v1"
MODEL_API_KEY_ENV = "MODEL_API_KEY"
SPARK_OPEN_BASE_URL_ENV = "SPARK_OPEN_BASE_URL"
SPARK_OPEN_API_KEY_ENV = "SPARK_OPEN_API_KEY"

# Fallback output budget per effort level for the pre-call cap estimate.
EFFORT_MAX_TOKENS = {"low": 512, "medium": 2048, "high": 8192}


@dataclass
class Completion:
    """Result of one chat completion."""

    text: str
    model: str
    provider: str
    in_tokens: int = 0
    out_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0


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

    def _client(self) -> httpx.Client:
        if self.client is not None:
            return self.client
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )

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
            "max_tokens": EFFORT_MAX_TOKENS.get(effort, 512),
        }
        try:
            response = self._client().post("/chat/completions", json=body)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(self.name, str(exc)) from exc
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
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(self.name, f"unexpected response shape: {data!r}") from exc
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens", 0) or 0
        in_tokens = usage.get("prompt_tokens", 0) or 0
        out_tokens = usage.get("completion_tokens", 0) or 0
        return Completion(
            text=text,
            model=data.get("model", self.model),
            provider=self.name,
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            cached_tokens=cached,
            cost_usd=cost_usd(self.model, in_tokens, out_tokens, cached),
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
    ) -> Completion:
        _ = (prompt, effort)
        usage = TokenUsage(
            model=self.model,
            in_tokens=self.in_tokens,
            out_tokens=self.out_tokens,
            cached_tokens=self.cached_tokens,
        )
        cost = 0.0
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
    """Hosted Spark Open client, or None when its credentials are absent."""
    base_url = os.environ.get(SPARK_OPEN_BASE_URL_ENV, "")
    api_key = os.environ.get(SPARK_OPEN_API_KEY_ENV, "")
    if not base_url or not api_key:
        return None
    return HttpChatProvider(
        name="spark-open-hosted",
        model=SPARK_OPEN_MODEL,
        base_url=base_url,
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
    )


__all__ = [
    "SPARK_CONTRIBUTOR_MODEL",
    "SPARK_MODEL",
    "SPARK_OPEN_MODEL",
    "ChatProvider",
    "Completion",
    "EFFORT_MAX_TOKENS",
    "FailingProvider",
    "FakeProvider",
    "HttpChatProvider",
    "ProviderError",
    "complete",
    "complete_with_fallback",
    "meta_provider",
    "spark_open_provider",
    "triage_provider",
]

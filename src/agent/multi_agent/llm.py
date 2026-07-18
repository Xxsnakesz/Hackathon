"""
Provider-agnostic LLM narrator — REQUIRED for the multi-agent team.

Providers, in order of preference (first one whose key is set wins):

  1. OpenAI     — requires OPENAI_API_KEY (supports OPENAI_BASE_URL gateways)
  2. Anthropic  — requires ANTHROPIC_API_KEY

There is no deterministic fallback: every agent's reasoning text comes from
the LLM. If no provider is configured, the orchestrator refuses to start and
the UI tells the user to set a key.

Tool execution stays in code (see tools.py) — the LLM reasons over real tool
output; it never fabricates drift data, lineage, or SQL. But all narrative
and the reviewer's final verdict are genuine LLM output.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("MultiAgent.LLM")

_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


class NarratorError(RuntimeError):
    """Raised when the LLM is unavailable or a call fails after retries."""


def detect_provider() -> str:
    """Return 'openai' | 'anthropic' | 'none'. Env vars decide."""
    forced = os.environ.get("LLM_PROVIDER", "").lower().strip()
    if forced in ("openai", "anthropic", "none"):
        return forced
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "none"


def get_model_name(provider: Optional[str] = None) -> str:
    provider = provider or detect_provider()
    if provider == "openai":
        return os.environ.get("OPENAI_MODEL", _DEFAULT_OPENAI_MODEL)
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL", _DEFAULT_ANTHROPIC_MODEL)
    return ""


def llm_available() -> bool:
    return detect_provider() != "none"


class Narrator:
    """
    Provider-agnostic narrator. `.narrate(system, user, fallback)` returns
    either the LLM output or `fallback` unchanged on any error.

    Kept intentionally small — orchestration/agent state lives in LangGraph,
    not here.
    """

    def __init__(self, provider: Optional[str] = None,
                 model: Optional[str] = None, max_tokens: int = 400):
        self.provider = provider or detect_provider()
        self.model = model or get_model_name(self.provider)
        self.max_tokens = max_tokens
        # OpenAI-compatible gateways (SumoPod, Azure-style proxies, local
        # vLLM/Ollama, etc.) need an explicit base_url — the OpenAI SDK
        # defaults to api.openai.com otherwise, which silently 401s/404s
        # against a key that was actually issued for a different gateway.
        self.base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
        self._client = None
        self._impl = "none"

        if self.provider == "openai":
            self._init_openai()
        elif self.provider == "anthropic":
            self._init_anthropic()

    # ── provider init ────────────────────────────────────────────────
    def _init_openai(self):
        try:
            from langchain_openai import ChatOpenAI
            kwargs = {"model": self.model, "max_tokens": self.max_tokens}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = ChatOpenAI(**kwargs)
            self._impl = "langchain-openai"
            logger.info(f"Narrator: langchain-openai / {self.model}"
                        + (f" @ {self.base_url}" if self.base_url else ""))
            return
        except Exception as exc:
            logger.info(f"langchain-openai unavailable ({exc}); trying raw openai SDK.")
        try:
            import openai
            kwargs = {}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = openai.OpenAI(**kwargs)
            self._impl = "openai"
            logger.info(f"Narrator: openai SDK / {self.model}"
                        + (f" @ {self.base_url}" if self.base_url else ""))
        except Exception as exc:
            logger.warning(f"OpenAI provider requested but no client available: {exc}")
            self._client = None

    def _init_anthropic(self):
        try:
            from langchain_anthropic import ChatAnthropic
            self._client = ChatAnthropic(model=self.model, max_tokens=self.max_tokens)
            self._impl = "langchain-anthropic"
            logger.info(f"Narrator: langchain-anthropic / {self.model}")
            return
        except Exception as exc:
            logger.info(f"langchain-anthropic unavailable ({exc}); trying raw anthropic SDK.")
        try:
            import anthropic
            self._client = anthropic.Anthropic()
            self._impl = "anthropic"
            logger.info(f"Narrator: anthropic SDK / {self.model}")
        except Exception as exc:
            logger.warning(f"Anthropic provider requested but no client available: {exc}")
            self._client = None

    def is_live(self) -> bool:
        return self._client is not None

    # ── main call ────────────────────────────────────────────────────
    def narrate(self, system: str, user: str, retries: int = 2) -> str:
        """
        Ask the LLM for a response. Retries transient failures once, then
        raises NarratorError — there is no template fallback by design.
        """
        if self._client is None:
            raise NarratorError(
                "No LLM provider configured. Set OPENAI_API_KEY (+ OPENAI_BASE_URL "
                "for gateways like SumoPod) or ANTHROPIC_API_KEY in .env."
            )
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                out = self._call_once(system, user)
                if out:
                    return out
                last_exc = NarratorError("LLM returned an empty response")
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"Narrator attempt {attempt}/{retries} failed "
                    f"({exc.__class__.__name__}: {exc})"
                )
        raise NarratorError(f"LLM call failed after {retries} attempt(s): {last_exc}")

    def _call_once(self, system: str, user: str) -> str:
        if self._impl in ("langchain-openai", "langchain-anthropic"):
            from langchain_core.messages import SystemMessage, HumanMessage
            resp = self._client.invoke([
                SystemMessage(content=system),
                HumanMessage(content=user),
            ])
            return (resp.content if isinstance(resp.content, str)
                    else "".join(p.get("text", "") for p in resp.content if isinstance(p, dict))).strip()

        if self._impl == "openai":
            resp = self._client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return (resp.choices[0].message.content or "").strip()

        if self._impl == "anthropic":
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
            return "".join(parts).strip()

        raise NarratorError(f"Unknown narrator implementation: {self._impl}")

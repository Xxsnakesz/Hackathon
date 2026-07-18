"""
Provider-agnostic narrator with deterministic fallback.

Providers, in order of preference (first one whose key is set wins):

  1. OpenAI     — requires OPENAI_API_KEY   (default; user has a GPT key)
  2. Anthropic  — requires ANTHROPIC_API_KEY
  3. None       — deterministic templates only (demo still completes end-to-end)

The wrapper prefers the LangChain adapters (`langchain_openai.ChatOpenAI`,
`langchain_anthropic.ChatAnthropic`) so we get the same message contract
regardless of provider — this is what LangGraph nodes want too. If LangChain
isn't installed, we fall back to the raw SDK for OpenAI (openai package),
and if neither is available we go deterministic silently.

Tool execution is ALWAYS deterministic (see tools.py). The narrator only
produces natural-language commentary; it never invents drift data, lineage,
or fix scripts.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("MultiAgent.LLM")

_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


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
            self._client = ChatOpenAI(model=self.model, max_tokens=self.max_tokens)
            self._impl = "langchain-openai"
            logger.info(f"Narrator: langchain-openai / {self.model}")
            return
        except Exception as exc:
            logger.info(f"langchain-openai unavailable ({exc}); trying raw openai SDK.")
        try:
            import openai
            self._client = openai.OpenAI()
            self._impl = "openai"
            logger.info(f"Narrator: openai SDK / {self.model}")
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
    def narrate(self, system: str, user: str, fallback: str) -> str:
        if self._client is None:
            return fallback
        try:
            if self._impl == "langchain-openai" or self._impl == "langchain-anthropic":
                from langchain_core.messages import SystemMessage, HumanMessage
                resp = self._client.invoke([
                    SystemMessage(content=system),
                    HumanMessage(content=user),
                ])
                out = (resp.content if isinstance(resp.content, str)
                       else "".join(p.get("text", "") for p in resp.content if isinstance(p, dict))).strip()
                return out or fallback

            if self._impl == "openai":
                resp = self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                out = (resp.choices[0].message.content or "").strip()
                return out or fallback

            if self._impl == "anthropic":
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
                out = "".join(parts).strip()
                return out or fallback

        except Exception as exc:
            logger.warning(f"Narrator call failed ({exc.__class__.__name__}: {exc}). Falling back.")

        return fallback


# ─────────────────────────────────────────────────────────────────────
# Back-compat alias — the previous version exported ClaudeNarrator.
# ─────────────────────────────────────────────────────────────────────
class ClaudeNarrator(Narrator):
    def __init__(self, model: Optional[str] = None, max_tokens: int = 400):
        super().__init__(provider="anthropic", model=model, max_tokens=max_tokens)

"""
generator/llm_client.py
Unified LLM client supporting Google Gemini and Anthropic Claude.

Provider selection (in order of precedence):
  1. LLM_PROVIDER env var: "gemini" | "claude" | "auto"
  2. "auto" tries Claude first (ANTHROPIC_API_KEY), then Gemini (GEMINI_API_KEY).

Only used for terraform.tfvars generation. All other Terraform files are
written deterministically — no LLM involved.
"""

import re
from agent.config import (
    LLM_PROVIDER,
    GEMINI_API_KEY, GEMINI_MODEL,
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    SYSTEM_PROMPT,
)


class LLMClient:
    """
    Thin wrapper around whichever LLM backend is active.
    Call .ask(prompt) → cleaned HCL string.
    """

    def __init__(self) -> None:
        self._backend = _resolve_backend()
        print(f"  🤖  LLM backend: {self._backend.name}")

    def ask(self, prompt: str) -> str:
        raw = self._backend.generate(prompt)
        return _strip_fences(raw)


# ── Backend resolution ─────────────────────────────────────────────────────

def _resolve_backend():
    if LLM_PROVIDER == "claude":
        return _make_claude()
    if LLM_PROVIDER == "gemini":
        return _make_gemini()
    # auto: prefer Claude, fall back to Gemini
    if ANTHROPIC_API_KEY:
        return _make_claude()
    if GEMINI_API_KEY:
        return _make_gemini()
    raise EnvironmentError(
        "No LLM API key found.\n"
        "Set ANTHROPIC_API_KEY (for Claude) or GEMINI_API_KEY (for Gemini).\n"
        "Optionally set LLM_PROVIDER=claude|gemini to force a specific backend."
    )


def _make_claude():
    if not ANTHROPIC_API_KEY:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set.\n"
            "Run: export ANTHROPIC_API_KEY='your-key'"
        )
    return _ClaudeBackend()


def _make_gemini():
    if not GEMINI_API_KEY:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set.\n"
            "Run: export GEMINI_API_KEY='your-key'"
        )
    return _GeminiBackend()


# ── Claude backend ─────────────────────────────────────────────────────────

class _ClaudeBackend:
    name = f"Anthropic Claude ({CLAUDE_MODEL})"

    def __init__(self) -> None:
        import anthropic
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def generate(self, prompt: str) -> str:
        msg = self._client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()


# ── Gemini backend ─────────────────────────────────────────────────────────

class _GeminiBackend:
    name = f"Google Gemini ({GEMINI_MODEL})"

    def __init__(self) -> None:
        from google import genai
        from google.genai import types

        self._client = genai.Client(api_key=GEMINI_API_KEY)
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        )

    def generate(self, prompt: str) -> str:
        resp = self._client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=self._config,
        )
        return resp.text.strip()


# ── Helpers ────────────────────────────────────────────────────────────────

def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
    s = re.sub(r"\n?```$", "", s)
    return s.strip()

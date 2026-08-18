"""
config.py — All configuration constants. Edit here, nowhere else.

Supported LLM providers:
  - gemini  : set GEMINI_API_KEY
  - claude  : set ANTHROPIC_API_KEY
  - auto    : tries Claude first, then Gemini (default)
"""

import os
from pathlib import Path

# ── LLM provider selection ─────────────────────────────────────────────────
# Set LLM_PROVIDER to "gemini", "claude", or "auto" (default).
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "auto").lower()

# API keys
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Model names
GEMINI_MODEL    = "gemini-2.5-flash"
CLAUDE_MODEL    = "claude-sonnet-4-20250514"

# ── Paths ──────────────────────────────────────────────────────────────────
AGENT_DIR  = Path(__file__).parent
OUTPUT_DIR = AGENT_DIR.parent / "terraform_output"

# ── System prompt for tfvars generation ───────────────────────────────────
SYSTEM_PROMPT = """You are a senior GCP Terraform engineer.
Generate ONLY valid HCL for terraform.tfvars.
Rules:
- Output raw HCL only — no markdown fences, no explanations.
- Map key = resource name. Never include a 'name' field inside the object.
- Every map key must be a valid HCL identifier: start with a letter,
  contain only letters, digits, underscores, hyphens.
- Keys with leading digits are INVALID — prefix them with 'r-'.
- No duplicate keys anywhere in the file.
"""

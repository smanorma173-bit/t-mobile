"""
utils/file_writer.py — Write Terraform files with post-processing.

Pipeline (every file):
  1. Normalise CRLF → LF
  2. Strip markdown fences
  3. each.value.name → each.key
  4. all([) → alltrue([
"""

import re
from pathlib import Path


def write(path: Path, content: str, relative_to: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_process(content) + "\n", encoding="utf-8")
    label = path.relative_to(relative_to) if relative_to else path
    print(f"  ✅  {label}")


def _process(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"^```[a-zA-Z]*\n?", "", s.strip())
    s = re.sub(r"\n?```$", "", s.strip())
    s = re.sub(r"\beach\.value\.name\b", "each.key", s)
    s = re.sub(r"\ball\s*\(\s*\[", "alltrue([", s)
    return s.strip()

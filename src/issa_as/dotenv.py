"""Tiny stdlib ``.env`` loader (no new dependency).

Parses ``KEY=VALUE`` lines (``#`` comments, blank lines, optional
``export`` prefix, single/double-quoted values). Real environment
variables always win: keys already present in ``os.environ`` are left
untouched. Never prints values — this module has no output at all.
"""

from __future__ import annotations

import os
from pathlib import Path


def parse_dotenv_text(text: str) -> dict[str, str]:
    """Parse dotenv content into a dict (no environment access)."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Load ``path`` into ``os.environ`` without overriding real env vars.

    Returns the parsed mapping (for tests); prints nothing and never
    touches keys that already exist in the environment. A missing file
    is not an error.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    parsed = parse_dotenv_text(text)
    for key, value in parsed.items():
        os.environ.setdefault(key, value)
    return parsed


__all__ = ["load_dotenv", "parse_dotenv_text"]

"""A tiny, careful `.env` reader for the settings CollectOS uses (such as ANTHROPIC_API_KEY).

Rules: only plain NAME=value lines are read; a real environment variable always wins; values are
never printed or logged; and a file other users on the machine can read earns a warning.
"""

import os
import re
import stat
from pathlib import Path

ALLOWED = ("ANTHROPIC_API_KEY", "COLLECTOS_AI_MODEL", "COLLECTOS_DB")
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env_file(path: str | Path = ".env", environ=None) -> list[str]:
    """Load allowed settings from the file into the environment. Returns warnings (never values)."""
    env = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return []
    warnings: list[str] = []
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            warnings.append(f"{path} can be read by other users of this computer; run: chmod 600 {path}")
    except OSError:
        pass
    seen_setting = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        match = _LINE.match(text)
        if not match:
            warnings.append(f"{path.name}: a line is not in NAME=value form and was ignored "
                            "(for the API key use a line like ANTHROPIC_API_KEY=your-key)")
            continue
        name, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name not in ALLOWED:
            continue
        seen_setting = True
        if value and name not in env:
            env[name] = value
    if not seen_setting and not warnings:
        warnings.append(f"{path.name} has no recognised settings (expected ANTHROPIC_API_KEY=...)")
    return warnings

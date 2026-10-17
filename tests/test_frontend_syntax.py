"""Every JavaScript file must at least parse. Skipped where Node is not installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "src" / "collectos"
FILES = sorted([*(ROOT / "webui").glob("*.js"), *(ROOT / "site").glob("*.js")])


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_javascript_parses(path):
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_there_are_files_to_check():
    assert {p.name for p in FILES} >= {"app.js", "features.js", "extras.js", "site.js"}

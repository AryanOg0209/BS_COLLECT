"""Tests never read the developer's real .env or API key, and never make real network calls."""

import pytest


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch):
    monkeypatch.setenv("COLLECTOS_NO_DOTENV", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COLLECTOS_AI_MODEL", raising=False)

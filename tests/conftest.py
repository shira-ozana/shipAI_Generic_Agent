from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from fakes import SECRET, FakeCursor, Responder
from pydantic import SecretStr

from generic_agent.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(api_key=SecretStr(SECRET), model="test-model")


@pytest.fixture
def mock_cursor(monkeypatch: pytest.MonkeyPatch) -> Callable[[Responder], FakeCursor]:
    """Install a fake Cursor SDK ``Agent`` and return it. No network, no bridge process."""

    def install(responder: Responder) -> FakeCursor:
        fake = FakeCursor(responder)
        monkeypatch.setattr("generic_agent.graph.Agent", SimpleNamespace(prompt=fake.prompt))
        return fake

    return install

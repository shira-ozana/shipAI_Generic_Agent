from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import SECRET, FakeCursor, Responder
from pydantic import SecretStr

from generic_agent.agents.task_split import HlsDocument, RepositoryAccess
from generic_agent.config import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "task_split"


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


@pytest.fixture
def hls_documents() -> list[HlsDocument]:
    """Two HLS documents describing one feature across the order and notification services."""
    return [
        HlsDocument("HLS-1", "Shipment dispatch notifications", (FIXTURES / "hls/shipment-notifications.md").read_text()),
        HlsDocument("HLS-2", "Event conventions", (FIXTURES / "hls/event-conventions.md").read_text()),
    ]


@pytest.fixture
def repository() -> RepositoryAccess:
    """The sample repository checkout at a declared revision."""
    return RepositoryAccess.from_checkout("shop", FIXTURES / "repo", revision="fixture-rev-1")


@pytest.fixture
def plan_reply() -> dict:
    """The expected model reply for the sample input (a fresh, mutable copy)."""
    return copy.deepcopy(json.loads((FIXTURES / "expected_reply.json").read_text()))

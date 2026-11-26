"""Offline tests for the HTTP API (Cursor SDK mocked, no network)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator

import pytest
from cursor_sdk import RateLimitError
from fakes import SECRET, FakeCursor, FakeRunResult, Responder
from fastapi.testclient import TestClient

from generic_agent.api import BUSY_MESSAGE, RUN_FAILED_MESSAGE, create_app
from generic_agent.config import Settings


@pytest.fixture
def make_client(
    settings: Settings, mock_cursor: Callable[[Responder], FakeCursor]
) -> Iterator[Callable[[Responder], tuple[TestClient, FakeCursor]]]:
    clients: list[TestClient] = []

    def make(responder: Responder) -> tuple[TestClient, FakeCursor]:
        fake = mock_cursor(responder)
        client = TestClient(create_app(settings))
        client.__enter__()  # run startup: compiles the graph once
        clients.append(client)
        return client, fake

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def chat(client: TestClient, thread_id: str, message: str):
    return client.post("/api/chat", json={"threadId": thread_id, "message": message})


def test_health_does_not_call_cursor(make_client) -> None:
    client, fake = make_client(lambda *_: "unused")
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake.calls == []


def test_chat_returns_reply(make_client) -> None:
    client, fake = make_client(lambda *_: "Hello from Cursor")
    thread_id = str(uuid.uuid4())
    response = chat(client, thread_id, "hi")
    assert response.status_code == 200
    assert response.json() == {"threadId": thread_id, "reply": "Hello from Cursor"}
    assert len(fake.calls) == 1
    assert "hi" in fake.prompts[0]
    options = fake.calls[0][1]
    assert options.model == "test-model"
    assert options.tools == []


def test_follow_up_uses_thread_history(make_client) -> None:
    client, fake = make_client(lambda message, _: f"answer {len(fake.calls)}")
    thread_id = str(uuid.uuid4())
    assert chat(client, thread_id, "My name is Dana").status_code == 200
    assert chat(client, thread_id, "What is my name?").json()["reply"] == "answer 2"
    assert "My name is Dana" not in fake.prompts[0].split("<current_user_message>")[0]
    second = fake.prompts[1]
    assert "<conversation_history>" in second
    assert "User: My name is Dana" in second
    assert "Assistant: answer 1" in second
    assert "What is my name?" in second.split("<current_user_message>")[1]


def test_threads_are_isolated(make_client) -> None:
    client, fake = make_client(lambda *_: "ok")
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    chat(client, first, "secret of thread one")
    response = chat(client, second, "hello from thread two")
    assert response.json()["threadId"] == second
    assert "secret of thread one" not in fake.prompts[1]
    assert "<conversation_history>" not in fake.prompts[1]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": "hi"},
        {"threadId": "not-a-uuid", "message": "hi"},
        {"threadId": 123, "message": "hi"},
        {"threadId": None, "message": "hi"},
        {"threadId": str(uuid.uuid4())},
        {"threadId": str(uuid.uuid4()), "message": ""},
        {"threadId": str(uuid.uuid4()), "message": "  \n\t "},
        {"threadId": str(uuid.uuid4()), "message": 5},
        {"threadId": str(uuid.uuid4()), "message": None},
    ],
)
def test_invalid_requests_are_rejected(make_client, payload) -> None:
    client, fake = make_client(lambda *_: "ok")
    assert client.post("/api/chat", json=payload).status_code == 422
    assert fake.calls == []


def test_non_json_body_is_rejected(make_client) -> None:
    client, fake = make_client(lambda *_: "ok")
    response = client.post("/api/chat", content=b"nope", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert fake.calls == []


def test_failed_run_returns_safe_502(make_client) -> None:
    def respond(*_):
        return FakeRunResult(status="error", error_message=f"boom {SECRET}")

    client, _ = make_client(respond)
    response = chat(client, str(uuid.uuid4()), "hi")
    assert response.status_code == 502
    assert response.json() == {"detail": RUN_FAILED_MESSAGE}
    assert SECRET not in response.text
    assert "boom" not in response.text


def test_sdk_exception_returns_safe_502(make_client) -> None:
    def respond(*_):
        raise RateLimitError(f"limit reached for {SECRET}")

    client, _ = make_client(respond)
    response = chat(client, str(uuid.uuid4()), "hi")
    assert response.status_code == 502
    assert response.json() == {"detail": RUN_FAILED_MESSAGE}
    assert SECRET not in response.text
    assert "limit reached" not in response.text


def test_empty_reply_returns_502(make_client) -> None:
    client, _ = make_client(lambda *_: "   ")
    response = chat(client, str(uuid.uuid4()), "hi")
    assert response.status_code == 502
    assert response.json() == {"detail": RUN_FAILED_MESSAGE}


def test_guard_is_released_after_failure(make_client) -> None:
    outcomes = iter([RuntimeError(f"bad {SECRET}"), "recovered"])

    def respond(*_):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client, _ = make_client(respond)
    thread_id = str(uuid.uuid4())
    assert chat(client, thread_id, "one").status_code == 502
    retry = chat(client, thread_id, "two")
    assert retry.status_code == 200
    assert retry.json()["reply"] == "recovered"


def test_concurrent_request_on_same_thread_gets_409(make_client) -> None:
    started, release = threading.Event(), threading.Event()

    def respond(message: str, _):
        if "slow" in message:
            started.set()
            assert release.wait(timeout=10)
        return "done"

    client, _ = make_client(respond)
    thread_id = str(uuid.uuid4())
    results: dict[str, int] = {}
    worker = threading.Thread(
        target=lambda: results.update(first=chat(client, thread_id, "slow").status_code)
    )
    worker.start()
    try:
        assert started.wait(timeout=10)
        busy = chat(client, thread_id, "again")
        assert busy.status_code == 409
        assert busy.json() == {"detail": BUSY_MESSAGE}
        # Other threads are not blocked by the running one.
        assert chat(client, str(uuid.uuid4()), "other").status_code == 200
    finally:
        release.set()
        worker.join(timeout=10)
    assert results["first"] == 200
    # The guard is released once the first request finishes.
    assert chat(client, thread_id, "after").status_code == 200


def test_cors_allows_local_dev_origin_only(make_client) -> None:
    client, _ = make_client(lambda *_: "ok")
    allowed = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.get("/api/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in denied.headers

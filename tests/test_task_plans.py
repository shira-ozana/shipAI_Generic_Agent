"""Review and approval flow: drafts never create tasks; only approving the displayed version does."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator

import pytest
from fakes import SECRET, FakeCursor, FakeRunResult, Responder
from fastapi.testclient import TestClient

from generic_agent.agents.task_split import (
    DependencyDocument,
    TaskSplitPlanner,
    parse_reply,
)
from generic_agent.api import create_app
from generic_agent.config import Settings
from generic_agent.task_plans import (
    DraftNotFoundError,
    DraftNotPendingError,
    InvalidDraftError,
    NotAuthorizedError,
    PlanAlreadyApprovedError,
    TaskPlanService,
    TaskPlanStore,
    UnknownRepositoryError,
    build_review,
)
from generic_agent.task_plans.routes import FORBIDDEN_MESSAGE, PLAN_FAILED_MESSAGE

ALICE, BOB = "alice", "bob"
EXPECTED_DEPENDENCIES = {("ORD-2", "ORD-1"), ("NOT-1", "ORD-1"), ("NOT-2", "ORD-2"), ("NOT-2", "NOT-1")}


@pytest.fixture
def store() -> Iterator[TaskPlanStore]:
    s = TaskPlanStore()
    yield s
    s.close()


@pytest.fixture
def document(plan_reply, repository) -> DependencyDocument:
    return parse_reply(json.dumps(plan_reply), hls_document_ids=["HLS-1", "HLS-2"], repository=repository)


def draft(store: TaskPlanStore, document: DependencyDocument, plan_id: str = "plan-1", user: str = ALICE, **kw):
    return store.create_draft(plan_id, document, repository_id="shop", revision="fixture-rev-1", created_by=user, **kw)


def without_task(document: DependencyDocument, task_id: str) -> DependencyDocument:
    """A revised document: ``task_id`` removed (and dropped from other tasks' dependencies)."""
    tasks = tuple(
        dataclasses.replace(t, depends_on=tuple(d for d in t.depends_on if d != task_id))
        for t in document.tasks
        if t.id != task_id
    )
    return dataclasses.replace(document, tasks=tasks)


def saved_dependencies(store: TaskPlanStore, plan_id: str = "plan-1") -> set[tuple[str, str]]:
    return {(t["id"], d) for t in store.saved_tasks(plan_id) for d in t["depends_on"]}


# --- drafts create no tasks ----------------------------------------------------------


def test_a_pending_draft_creates_zero_task_records(store, document) -> None:
    record = draft(store, document)

    assert record.status == "pending" and record.version == 1
    assert store.count_tasks() == 0 and store.count_dependencies() == 0
    assert store.get_draft("plan-1").document == document  # retained as a review artifact


def test_a_declined_draft_creates_zero_task_records_and_cannot_be_approved(store, document) -> None:
    draft(store, document)

    declined = store.decline("plan-1", 1, ALICE)
    assert declined.status == "declined" and declined.decided_by == ALICE
    assert store.decline("plan-1", 1, ALICE).status == "declined"  # declining twice is a no-op

    with pytest.raises(DraftNotPendingError, match="declined"):
        store.approve("plan-1", 1, ALICE)
    assert store.count_tasks() == 0 and store.count_dependencies() == 0
    assert store.get_draft("plan-1", 1).status == "declined"


def test_a_superseded_draft_creates_zero_task_records_and_only_the_new_version_can_be_approved(
    store, document
) -> None:
    draft(store, document)
    revised = draft(store, without_task(document, "NOT-2"))  # a revised document is a new version
    assert revised.version == 2
    assert store.get_draft("plan-1", 1).status == "superseded"
    assert store.count_tasks() == 0

    with pytest.raises(DraftNotPendingError, match="superseded"):
        store.approve("plan-1", 1, ALICE)
    assert store.count_tasks() == 0

    store.approve("plan-1", 2, ALICE)
    saved = store.saved_tasks("plan-1")
    assert {t["plan_version"] for t in saved} == {2}
    assert {t["id"] for t in saved} == {"ORD-1", "ORD-2", "NOT-1"}  # the displayed v2, not v1
    assert store.get_draft("plan-1", 1).status == "superseded"


def test_a_declined_version_stays_declined_when_a_new_one_is_created(store, document) -> None:
    draft(store, document)
    store.decline("plan-1", 1, ALICE)
    draft(store, document)
    assert [store.get_draft("plan-1", v).status for v in (1, 2)] == ["declined", "pending"]


# --- approval saves exactly the displayed version -------------------------------------


def test_approval_saves_tasks_groups_and_cross_group_dependencies_of_that_version(store, document) -> None:
    draft(store, document)

    result = store.approve("plan-1", 1, ALICE)

    assert (result.task_count, result.dependency_count, result.already_approved) == (4, 4, False)
    saved = {t["id"]: t for t in store.saved_tasks("plan-1")}
    assert {i: t["group_id"] for i, t in saved.items()} == {
        "ORD-1": "order-service",
        "ORD-2": "order-service",
        "NOT-1": "notification-service",
        "NOT-2": "notification-service",
    }
    assert saved["ORD-1"]["deliverable"] == "contracts/shipment_dispatched.json"
    assert saved_dependencies(store) == EXPECTED_DEPENDENCIES
    approved = store.get_draft("plan-1", 1)
    assert approved.status == "approved" and approved.decided_by == ALICE and approved.decided_at


def test_approval_is_bound_to_the_version_the_user_saw(store, document) -> None:
    draft(store, document)
    with pytest.raises(DraftNotFoundError):
        store.approve("plan-1", 2, ALICE)  # a version that was never displayed
    with pytest.raises(DraftNotFoundError):
        store.approve("other-plan", 1, ALICE)
    assert store.count_tasks() == 0 and store.get_draft("plan-1").status == "pending"


# --- retries -----------------------------------------------------------------------------


def test_retrying_approval_creates_one_set_of_records(store, document) -> None:
    draft(store, document)

    first = store.approve("plan-1", 1, ALICE)
    second = store.approve("plan-1", 1, ALICE)
    third = store.approve("plan-1", 1, ALICE)

    assert (first.already_approved, second.already_approved, third.already_approved) == (False, True, True)
    assert (second.task_count, second.dependency_count) == (4, 4)
    assert store.count_tasks() == 4 and store.count_dependencies() == 4
    assert saved_dependencies(store) == EXPECTED_DEPENDENCIES


def test_concurrent_approvals_of_the_same_version_create_one_set_of_records(tmp_path, document) -> None:
    store = TaskPlanStore(str(tmp_path / "plans.sqlite3"))
    draft(store, document)
    results, errors = [], []
    barrier = threading.Barrier(8, timeout=5)

    def go() -> None:
        barrier.wait()
        try:
            results.append(store.approve("plan-1", 1, ALICE))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [threading.Thread(target=go) for _ in range(8)]
    [w.start() for w in workers]
    [w.join(timeout=10) for w in workers]

    assert errors == []
    assert sum(not r.already_approved for r in results) == 1 and len(results) == 8
    assert store.count_tasks() == 4 and store.count_dependencies() == 4
    store.close()


def test_records_survive_reopening_the_database_and_retries_stay_idempotent(tmp_path, document) -> None:
    path = str(tmp_path / "plans.sqlite3")
    first = TaskPlanStore(path)
    draft(first, document)
    first.approve("plan-1", 1, ALICE)
    first.close()

    reopened = TaskPlanStore(path)
    assert reopened.approve("plan-1", 1, ALICE).already_approved is True
    assert reopened.count_tasks() == 4
    reopened.close()


def test_an_approved_plan_accepts_no_new_drafts(store, document) -> None:
    draft(store, document)
    store.approve("plan-1", 1, ALICE)
    with pytest.raises(PlanAlreadyApprovedError):
        draft(store, document)
    assert store.count_tasks() == 4
    with pytest.raises(DraftNotPendingError):
        store.decline("plan-1", 1, ALICE)  # approved drafts cannot be declined


# --- authorization --------------------------------------------------------------------------


def test_only_authorized_users_can_approve_or_decline(store, document) -> None:
    draft(store, document)

    for action in (store.approve, store.decline):
        with pytest.raises(NotAuthorizedError):
            action("plan-1", 1, BOB)
    assert store.count_tasks() == 0 and store.get_draft("plan-1").status == "pending"

    draft(store, document, plan_id="plan-2", approvers=[BOB])  # creator plus a named approver
    store.approve("plan-2", 1, BOB)
    assert store.count_tasks("plan-2") == 4


def test_only_authorized_users_can_revise_a_plan(store, document) -> None:
    draft(store, document)
    with pytest.raises(NotAuthorizedError):
        draft(store, document, user=BOB)
    assert store.get_draft("plan-1").version == 1


# --- invalid drafts are rejected before approval ----------------------------------------------


@pytest.mark.parametrize(
    ("task_id", "change", "message"),
    [
        ("NOT-1", {"depends_on": ("ORD-404",)}, "unknown task"),
        ("ORD-1", {"depends_on": ("NOT-2",)}, "dependency cycle"),
        ("NOT-1", {"group_id": "ghost"}, "unknown group"),
        ("NOT-2", {"id": "ORD-1"}, "duplicate task ID"),
    ],
)
def test_invalid_documents_are_rejected_before_they_can_be_approved(store, document, task_id, change, message) -> None:
    tasks = tuple(dataclasses.replace(t, **change) if t.id == task_id else t for t in document.tasks)
    bad = dataclasses.replace(document, tasks=tasks)

    with pytest.raises(InvalidDraftError, match=message):
        draft(store, bad)
    with pytest.raises(DraftNotFoundError):
        store.get_draft("plan-1")  # not even stored as a draft
    assert store.count_tasks() == 0


def test_a_stored_document_that_became_invalid_cannot_be_approved(store, document) -> None:
    draft(store, document)
    tampered = document.to_dict()
    tampered["tasks"][0]["depends_on"] = ["NOT-2"]  # ORD-1 -> NOT-2 -> ... -> ORD-1
    store._db.execute("UPDATE plan_drafts SET document = ?", (json.dumps(tampered),))

    with pytest.raises(InvalidDraftError, match="cycle"):
        store.approve("plan-1", 1, ALICE)
    assert store.count_tasks() == 0 and store.get_draft("plan-1").status == "pending"


def test_a_failure_while_saving_rolls_back_everything(store, document, monkeypatch) -> None:
    draft(store, document)

    def explode(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "_insert_dependencies", explode)
    with pytest.raises(sqlite3.OperationalError):
        store.approve("plan-1", 1, ALICE)  # groups and tasks were already inserted when this failed

    assert store.count_tasks() == 0 and store.count_dependencies() == 0
    assert store._db.execute("SELECT COUNT(*) FROM task_groups").fetchone()[0] == 0
    assert store.get_draft("plan-1").status == "pending"  # the status change was rolled back too

    monkeypatch.undo()
    assert store.approve("plan-1", 1, ALICE).task_count == 4  # and a retry then succeeds once


# --- review view ---------------------------------------------------------------------------------


def test_review_groups_tasks_by_owner_and_shows_cross_group_dependencies(store, document) -> None:
    review = build_review(draft(store, document))

    assert (review["planId"], review["version"], review["status"]) == ("plan-1", 1, "pending")
    assert review["repository"] == {"id": "shop", "revision": "fixture-rev-1"}
    groups = {g["id"]: g for g in review["groups"]}
    assert [t["id"] for t in groups["order-service"]["tasks"]] == ["ORD-1", "ORD-2"]
    assert [t["id"] for t in groups["notification-service"]["tasks"]] == ["NOT-1", "NOT-2"]
    assert review["crossGroupDependencies"] == [
        {"taskId": "NOT-1", "groupId": "notification-service", "dependsOnTaskId": "ORD-1", "dependsOnGroupId": "order-service"},
        {"taskId": "NOT-2", "groupId": "notification-service", "dependsOnTaskId": "ORD-2", "dependsOnGroupId": "order-service"},
    ]
    not_1 = groups["notification-service"]["tasks"][0]
    assert not_1["dependsOn"] == [{"taskId": "ORD-1", "groupId": "order-service", "crossGroup": True}]
    ord_2 = groups["order-service"]["tasks"][1]
    assert ord_2["dependsOn"] == [{"taskId": "ORD-1", "groupId": "order-service", "crossGroup": False}]
    assert review["executionWaves"] == [["ORD-1"], ["ORD-2", "NOT-1"], ["NOT-2"]]
    assert groups["order-service"]["tasks"][0]["canStartImmediately"] is True
    assert review["warnings"] == [] and review["discrepancies"][0]["repoPaths"] == ["services/order/status.py"]


# --- service and HTTP API ---------------------------------------------------------------------------


@pytest.fixture
def service(settings: Settings, repository, store: TaskPlanStore) -> TaskPlanService:
    return TaskPlanService(store, TaskSplitPlanner(settings), {"shop": repository})


@pytest.fixture
def make_client(
    settings: Settings, mock_cursor: Callable[[Responder], FakeCursor], service: TaskPlanService
) -> Iterator[Callable[[Responder], tuple[TestClient, FakeCursor]]]:
    clients: list[TestClient] = []

    def make(responder: Responder) -> tuple[TestClient, FakeCursor]:
        fake = mock_cursor(responder)
        client = TestClient(create_app(settings, task_plans=service))
        client.__enter__()
        clients.append(client)
        return client, fake

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def body(hls_documents, **extra) -> dict:
    return {
        "repositoryId": "shop",
        "hlsDocuments": [{"id": d.id, "title": d.title, "content": d.content} for d in hls_documents],
        **extra,
    }


def as_user(user: str) -> dict:
    return {"X-User-Id": user}


def test_service_plan_ends_with_a_pending_draft_and_no_tasks(service, mock_cursor, hls_documents, plan_reply) -> None:
    mock_cursor(lambda p, o: json.dumps(plan_reply))

    result = service.plan(ALICE, hls_documents, "shop")

    assert result.draft is not None and result.draft.status == "pending"
    assert result.draft.revision == "fixture-rev-1"  # the inspected revision is recorded with the draft
    assert service.store.count_tasks() == 0


def test_service_rejects_unknown_repositories_before_running_the_agent(service, mock_cursor, hls_documents) -> None:
    cursor = mock_cursor(lambda p, o: "unused")
    with pytest.raises(UnknownRepositoryError):
        service.plan(ALICE, hls_documents, "someone-elses-repo")
    assert cursor.calls == []


def test_http_flow_draft_review_approve_and_retry(make_client, service, hls_documents, plan_reply) -> None:
    client, fake = make_client(lambda p, o: json.dumps(plan_reply))

    created = client.post("/api/task-plans", json=body(hls_documents), headers=as_user(ALICE))
    assert created.status_code == 200
    payload = created.json()
    assert payload["status"] == "draft"
    review, plan_id = payload["draft"], payload["planId"]
    assert (review["version"], review["status"]) == (1, "pending")
    assert [g["id"] for g in review["groups"]] == ["order-service", "notification-service"]
    assert len(review["crossGroupDependencies"]) == 2
    assert service.store.count_tasks() == 0  # the planning run ends with a draft

    approve = f"/api/task-plans/{plan_id}/versions/{review['version']}/approve"
    first = client.post(approve, headers=as_user(ALICE))
    retry = client.post(approve, headers=as_user(ALICE))

    assert first.status_code == retry.status_code == 200
    assert first.json() == {
        "status": "approved", "planId": plan_id, "version": 1,
        "taskCount": 4, "dependencyCount": 4, "alreadyApproved": False,
    }
    assert retry.json()["alreadyApproved"] is True
    assert service.store.count_tasks() == 4 and service.store.count_dependencies() == 4
    assert saved_dependencies(service.store, plan_id) == EXPECTED_DEPENDENCIES
    assert client.get(f"/api/task-plans/{plan_id}", headers=as_user(ALICE)).json()["status"] == "approved"
    assert len(fake.calls) == 1


def test_http_pending_declined_and_superseded_drafts_save_zero_tasks(
    make_client, service, hls_documents, plan_reply
) -> None:
    client, _ = make_client(lambda p, o: json.dumps(plan_reply))
    first = client.post("/api/task-plans", json=body(hls_documents), headers=as_user(ALICE)).json()
    plan_id = first["planId"]
    assert service.store.count_tasks() == 0  # pending

    second = client.post(
        "/api/task-plans", json=body(hls_documents, planId=plan_id, notes="Split NOT-1 differently."),
        headers=as_user(ALICE),
    ).json()
    assert second["draft"]["version"] == 2

    stale = client.post(f"/api/task-plans/{plan_id}/versions/1/approve", headers=as_user(ALICE))
    assert stale.status_code == 409 and "superseded" in stale.json()["detail"]
    assert service.store.count_tasks() == 0  # superseded

    declined = client.post(f"/api/task-plans/{plan_id}/versions/2/decline", headers=as_user(ALICE))
    assert declined.status_code == 200 and declined.json()["status"] == "declined"
    after_decline = client.post(f"/api/task-plans/{plan_id}/versions/2/approve", headers=as_user(ALICE))
    assert after_decline.status_code == 409
    assert service.store.count_tasks() == 0  # declined


def test_http_approval_requires_the_authorized_user(make_client, service, hls_documents, plan_reply) -> None:
    client, _ = make_client(lambda p, o: json.dumps(plan_reply))
    plan_id = client.post("/api/task-plans", json=body(hls_documents), headers=as_user(ALICE)).json()["planId"]
    url = f"/api/task-plans/{plan_id}/versions/1/approve"

    assert client.post(url).status_code == 401  # no identity
    wrong = client.post(url, headers=as_user(BOB))
    assert wrong.status_code == 403 and wrong.json() == {"detail": FORBIDDEN_MESSAGE}
    assert client.get(f"/api/task-plans/{plan_id}", headers=as_user(BOB)).status_code == 404  # not visible
    assert client.post(f"/api/task-plans/{plan_id}/versions/9/approve", headers=as_user(ALICE)).status_code == 404
    assert service.store.count_tasks() == 0


def test_http_invalid_dependencies_are_rejected_before_anything_can_be_approved(
    make_client, service, hls_documents, plan_reply
) -> None:
    plan_reply["plan"]["tasks"][2]["depends_on"] = ["ORD-404"]  # missing task
    client, _ = make_client(lambda p, o: json.dumps(plan_reply))

    response = client.post("/api/task-plans", json=body(hls_documents, planId="plan-x"), headers=as_user(ALICE))

    assert response.status_code == 502 and response.json() == {"detail": PLAN_FAILED_MESSAGE}
    assert "ORD-404" not in response.text
    assert client.get("/api/task-plans/plan-x", headers=as_user(ALICE)).status_code == 404  # no draft exists
    assert client.post("/api/task-plans/plan-x/versions/1/approve", headers=as_user(ALICE)).status_code == 404
    assert service.store.count_tasks() == 0

    plan_reply["plan"]["tasks"][2]["depends_on"] = ["NOT-2"]  # a cycle: NOT-1 -> NOT-2 -> NOT-1
    assert client.post("/api/task-plans", json=body(hls_documents, planId="plan-x"), headers=as_user(ALICE)).status_code == 502
    assert service.store.count_tasks() == 0


def test_http_clarification_is_returned_without_a_draft(make_client, service, hls_documents) -> None:
    question = {
        "status": "clarification",
        "questions": [{"question": "Who owns the tracking link?", "why_it_matters": "It sets the contract owner."}],
    }
    client, _ = make_client(lambda p, o: json.dumps(question))

    response = client.post("/api/task-plans", json=body(hls_documents), headers=as_user(ALICE))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "clarification" and "draft" not in payload
    assert payload["questions"][0]["question"] == "Who owns the tracking link?"
    assert client.get(f"/api/task-plans/{payload['planId']}", headers=as_user(ALICE)).status_code == 404
    assert service.store.count_tasks() == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"repositoryId": "shop"},
        {"repositoryId": "shop", "hlsDocuments": []},
        {"repositoryId": "shop", "hlsDocuments": [{"id": "HLS-1", "title": "t"}]},
        {"repositoryId": "shop", "hlsDocuments": [{"id": "bad id", "title": "t", "content": "c"}]},
        {"repositoryId": "shop", "hlsDocuments": [{"id": "HLS-1", "title": "t", "content": "  "}]},
        {"repositoryId": "shop", "hlsDocuments": [{"id": "HLS-1", "title": "t", "content": "key https://u:pw@h/x"}]},
    ],
)
def test_http_invalid_requests_never_start_a_run(make_client, payload) -> None:
    client, fake = make_client(lambda p, o: "unused")
    assert client.post("/api/task-plans", json=payload, headers=as_user(ALICE)).status_code == 422
    assert fake.calls == []


def test_http_unknown_repository_and_failed_runs_are_safe(make_client, hls_documents) -> None:
    def failing(*_):
        return FakeRunResult(status="error", error_message=f"boom {SECRET}")

    client, _ = make_client(failing)
    unknown = client.post("/api/task-plans", json={**body(hls_documents), "repositoryId": "nope"}, headers=as_user(ALICE))
    assert unknown.status_code == 404

    failed = client.post("/api/task-plans", json=body(hls_documents), headers=as_user(ALICE))
    assert failed.status_code == 502 and failed.json() == {"detail": PLAN_FAILED_MESSAGE}
    assert SECRET not in failed.text and "boom" not in failed.text


def test_task_plan_endpoints_answer_503_when_not_configured(settings, monkeypatch, tmp_path, hls_documents) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TASK_SPLIT_REPOSITORIES", raising=False)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/task-plans", json=body(hls_documents), headers=as_user(ALICE))
        assert response.status_code == 503
        assert client.get("/api/health").status_code == 200


def test_cors_allows_the_user_header_from_local_origins(make_client) -> None:
    client, _ = make_client(lambda p, o: "unused")
    preflight = client.options(
        "/api/task-plans",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-user-id",
        },
    )
    assert preflight.status_code == 200
    assert "x-user-id" in preflight.headers["access-control-allow-headers"].lower()


def test_app_builds_the_task_plan_service_from_environment_settings(
    settings, mock_cursor, monkeypatch, tmp_path, hls_documents, plan_reply
) -> None:
    from conftest import FIXTURES

    db = tmp_path / "plans.sqlite3"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "TASK_SPLIT_REPOSITORIES",
        json.dumps({"shop": {"path": str(FIXTURES / "repo"), "revision": "fixture-rev-1"}}),
    )
    monkeypatch.setenv("TASK_SPLIT_DB", str(db))
    mock_cursor(lambda p, o: json.dumps(plan_reply))

    with TestClient(create_app(settings)) as client:
        created = client.post("/api/task-plans", json=body(hls_documents), headers=as_user(ALICE))
        assert created.status_code == 200
        plan_id = created.json()["planId"]
        assert client.post(f"/api/task-plans/{plan_id}/versions/1/approve", headers=as_user(ALICE)).status_code == 200

    reopened = TaskPlanStore(str(db))  # persisted in the configured database file
    assert reopened.count_tasks(plan_id) == 4
    reopened.close()

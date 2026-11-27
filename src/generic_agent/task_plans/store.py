"""SQLite persistence for draft plans and the tasks created by approving one.

Invariants (all enforced here, in the backend):

* Drafts are artifacts. Creating, declining or superseding a draft never writes
  a task row. Tasks, groups and dependencies are written only by ``approve``.
* ``approve(plan_id, version, user)`` checks that ``user`` is an authorized
  approver, that the draft is *that exact version*, and that it is pending (or
  already approved: a retry). It then writes the status change, groups, tasks
  and dependencies in **one transaction**; any failure rolls everything back.
* The plan version is the idempotency key: tasks are keyed by
  ``(plan_id, plan_version, task_id)``, a repeated approval of the same version
  returns the existing result and inserts nothing, and a plan that already has
  an approved version accepts no further drafts.
* A new draft supersedes earlier pending drafts of the same plan; a superseded
  or declined version can never be approved.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from task_split_agent.schema import (
    DependencyDocument,
    PlanValidationError,
    validate_document,
)

PENDING = "pending"
APPROVED = "approved"
DECLINED = "declined"
SUPERSEDED = "superseded"

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    plan_id   TEXT PRIMARY KEY,
    approvers TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_drafts (
    plan_id         TEXT NOT NULL REFERENCES plans(plan_id),
    version         INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending','approved','declined','superseded')),
    document        TEXT NOT NULL,
    repository_id   TEXT NOT NULL,
    revision        TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    decided_by      TEXT,
    decided_at      TEXT,
    PRIMARY KEY (plan_id, version)
);
CREATE TABLE IF NOT EXISTS task_groups (
    plan_id      TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    group_id     TEXT NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    proposed     INTEGER NOT NULL,
    hls_references TEXT NOT NULL,
    repo_paths   TEXT NOT NULL,
    PRIMARY KEY (plan_id, plan_version, group_id),
    FOREIGN KEY (plan_id, plan_version) REFERENCES plan_drafts(plan_id, version)
);
CREATE TABLE IF NOT EXISTS tasks (
    plan_id      TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    task_id      TEXT NOT NULL,
    group_id     TEXT NOT NULL,
    title        TEXT NOT NULL,
    scope        TEXT NOT NULL,
    deliverable  TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    hls_references TEXT NOT NULL,
    repo_references TEXT NOT NULL,
    PRIMARY KEY (plan_id, plan_version, task_id),
    FOREIGN KEY (plan_id, plan_version, group_id)
        REFERENCES task_groups(plan_id, plan_version, group_id)
);
CREATE TABLE IF NOT EXISTS task_dependencies (
    plan_id         TEXT NOT NULL,
    plan_version    INTEGER NOT NULL,
    task_id         TEXT NOT NULL,
    depends_on_id   TEXT NOT NULL,
    PRIMARY KEY (plan_id, plan_version, task_id, depends_on_id),
    FOREIGN KEY (plan_id, plan_version, task_id) REFERENCES tasks(plan_id, plan_version, task_id),
    FOREIGN KEY (plan_id, plan_version, depends_on_id) REFERENCES tasks(plan_id, plan_version, task_id)
);
"""


class TaskPlanError(Exception):
    """Base class for store and service errors the API maps to HTTP statuses."""


class DraftNotFoundError(TaskPlanError):
    """No such plan or draft version."""


class NotAuthorizedError(TaskPlanError):
    """The user may not act on this plan."""


class DraftNotPendingError(TaskPlanError):
    """The draft was declined or superseded (or approved when declining)."""

    def __init__(self, status: str) -> None:
        super().__init__(f"draft is {status}")
        self.status = status


class PlanAlreadyApprovedError(TaskPlanError):
    """The plan already has an approved version; revising it is not supported."""


class InvalidDraftError(TaskPlanError):
    """The document breaks the dependency-document rules (e.g. missing dependency, cycle)."""

    def __init__(self, error: PlanValidationError) -> None:
        super().__init__(str(error))
        self.errors = error.errors


@dataclass(frozen=True)
class DraftRecord:
    plan_id: str
    version: int
    status: str
    document: DependencyDocument
    repository_id: str
    revision: str
    created_by: str
    created_at: str
    decided_by: str | None = None
    decided_at: str | None = None


@dataclass(frozen=True)
class ApprovalResult:
    plan_id: str
    version: int
    task_count: int
    dependency_count: int
    already_approved: bool


class TaskPlanStore:
    """Thread-safe SQLite store. Use ``":memory:"`` in tests."""

    def __init__(self, path: str = ":memory:") -> None:
        # One shared connection, serialized by a lock; transactions are explicit.
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    # --- drafts ----------------------------------------------------------------

    def create_draft(
        self,
        plan_id: str,
        document: DependencyDocument,
        *,
        repository_id: str,
        revision: str,
        created_by: str,
        approvers: Collection[str] | None = None,
    ) -> DraftRecord:
        """Store ``document`` as the next pending version and supersede older pending ones.

        The first draft of a plan fixes its approvers (default: the creator).
        Later drafts must come from one of them. No task rows are written.
        """
        try:
            validate_document(document)
        except PlanValidationError as exc:
            raise InvalidDraftError(exc) from exc
        with self._transaction() as db:
            plan = db.execute("SELECT approvers FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if plan is None:
                allowed = sorted({created_by, *(approvers or ())})
                db.execute("INSERT INTO plans VALUES (?, ?)", (plan_id, json.dumps(allowed)))
            elif created_by not in json.loads(plan["approvers"]):
                raise NotAuthorizedError("user may not revise this plan")
            if db.execute(
                "SELECT 1 FROM plan_drafts WHERE plan_id = ? AND status = ?", (plan_id, APPROVED)
            ).fetchone():
                raise PlanAlreadyApprovedError("plan already has an approved version")
            version = db.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM plan_drafts WHERE plan_id = ?", (plan_id,)
            ).fetchone()[0]
            db.execute(
                "UPDATE plan_drafts SET status = ? WHERE plan_id = ? AND status = ?",
                (SUPERSEDED, plan_id, PENDING),
            )
            now = _now()
            db.execute(
                "INSERT INTO plan_drafts (plan_id, version, status, document, repository_id, revision,"
                " created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (plan_id, version, PENDING, json.dumps(document.to_dict()), repository_id, revision,
                 created_by, now),
            )
        return DraftRecord(plan_id, version, PENDING, document, repository_id, revision, created_by, now)

    def get_draft(self, plan_id: str, version: int | None = None) -> DraftRecord:
        """A specific version, or the latest when ``version`` is ``None``."""
        with self._lock:
            if version is None:
                row = self._db.execute(
                    "SELECT * FROM plan_drafts WHERE plan_id = ? ORDER BY version DESC LIMIT 1", (plan_id,)
                ).fetchone()
            else:
                row = self._db.execute(
                    "SELECT * FROM plan_drafts WHERE plan_id = ? AND version = ?", (plan_id, version)
                ).fetchone()
        if row is None:
            raise DraftNotFoundError("draft not found")
        return _draft(row)

    def is_authorized(self, plan_id: str, user: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT approvers FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        return row is not None and user in json.loads(row["approvers"])

    # --- decisions -------------------------------------------------------------

    def decline(self, plan_id: str, version: int, user: str) -> DraftRecord:
        """Mark a pending draft declined. Declining twice is a no-op. Writes no tasks."""
        with self._transaction() as db:
            row = self._locked_draft(db, plan_id, version, user)
            if row["status"] == PENDING:
                db.execute(
                    "UPDATE plan_drafts SET status = ?, decided_by = ?, decided_at = ?"
                    " WHERE plan_id = ? AND version = ?",
                    (DECLINED, user, _now(), plan_id, version),
                )
            elif row["status"] != DECLINED:
                raise DraftNotPendingError(row["status"])
        return self.get_draft(plan_id, version)

    def approve(self, plan_id: str, version: int, user: str) -> ApprovalResult:
        """Approve exactly ``version`` and save its tasks and dependencies atomically.

        Repeating the call for an already-approved version returns the stored
        result without inserting anything.
        """
        with self._transaction() as db:
            row = self._locked_draft(db, plan_id, version, user)
            if row["status"] == APPROVED:
                return self._result(db, plan_id, version, already=True)
            if row["status"] != PENDING:
                raise DraftNotPendingError(row["status"])
            document = DependencyDocument.from_dict(json.loads(row["document"]))
            try:
                validate_document(document)  # re-check what is about to be persisted
            except PlanValidationError as exc:
                raise InvalidDraftError(exc) from exc
            db.execute(
                "UPDATE plan_drafts SET status = ?, decided_by = ?, decided_at = ?"
                " WHERE plan_id = ? AND version = ?",
                (APPROVED, user, _now(), plan_id, version),
            )
            self._insert_records(db, plan_id, version, document)
            return self._result(db, plan_id, version, already=False)

    def _locked_draft(self, db: sqlite3.Connection, plan_id: str, version: int, user: str) -> sqlite3.Row:
        plan = db.execute("SELECT approvers FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if plan is None:
            raise DraftNotFoundError("draft not found")
        if user not in json.loads(plan["approvers"]):
            raise NotAuthorizedError("user may not decide on this plan")
        row = db.execute(
            "SELECT * FROM plan_drafts WHERE plan_id = ? AND version = ?", (plan_id, version)
        ).fetchone()
        if row is None:
            raise DraftNotFoundError("draft not found")
        return row

    def _insert_records(
        self, db: sqlite3.Connection, plan_id: str, version: int, document: DependencyDocument
    ) -> None:
        for group in document.groups:
            db.execute(
                "INSERT INTO task_groups VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (plan_id, version, group.id, group.name, group.kind, int(group.proposed),
                 json.dumps(group.hls_references), json.dumps(group.repo_paths)),
            )
        for task in document.tasks:
            db.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (plan_id, version, task.id, task.group_id, task.title, task.scope, task.deliverable,
                 json.dumps(task.acceptance_criteria), json.dumps(task.hls_references),
                 json.dumps(task.repo_references)),
            )
        self._insert_dependencies(db, plan_id, version, document)

    def _insert_dependencies(
        self, db: sqlite3.Connection, plan_id: str, version: int, document: DependencyDocument
    ) -> None:
        for task in document.tasks:
            for dep in task.depends_on:
                db.execute(
                    "INSERT INTO task_dependencies VALUES (?, ?, ?, ?)", (plan_id, version, task.id, dep)
                )

    def _result(self, db: sqlite3.Connection, plan_id: str, version: int, *, already: bool) -> ApprovalResult:
        tasks = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE plan_id = ? AND plan_version = ?", (plan_id, version)
        ).fetchone()[0]
        deps = db.execute(
            "SELECT COUNT(*) FROM task_dependencies WHERE plan_id = ? AND plan_version = ?",
            (plan_id, version),
        ).fetchone()[0]
        return ApprovalResult(plan_id, version, tasks, deps, already)

    # --- reads of persisted records ---------------------------------------------

    def count_tasks(self, plan_id: str | None = None) -> int:
        with self._lock:
            if plan_id is None:
                return self._db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            return self._db.execute("SELECT COUNT(*) FROM tasks WHERE plan_id = ?", (plan_id,)).fetchone()[0]

    def saved_tasks(self, plan_id: str) -> list[dict[str, Any]]:
        """Persisted tasks of a plan with their dependency IDs, ordered by ID."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE plan_id = ? ORDER BY plan_version, task_id", (plan_id,)
            ).fetchall()
            deps = self._db.execute(
                "SELECT plan_version, task_id, depends_on_id FROM task_dependencies WHERE plan_id = ?"
                " ORDER BY depends_on_id",
                (plan_id,),
            ).fetchall()
        by_task: dict[tuple[int, str], list[str]] = {}
        for dep in deps:
            by_task.setdefault((dep["plan_version"], dep["task_id"]), []).append(dep["depends_on_id"])
        return [
            {
                "plan_version": r["plan_version"],
                "id": r["task_id"],
                "group_id": r["group_id"],
                "title": r["title"],
                "deliverable": r["deliverable"],
                "depends_on": by_task.get((r["plan_version"], r["task_id"]), []),
            }
            for r in rows
        ]

    def count_dependencies(self, plan_id: str | None = None) -> int:
        with self._lock:
            if plan_id is None:
                return self._db.execute("SELECT COUNT(*) FROM task_dependencies").fetchone()[0]
            return self._db.execute(
                "SELECT COUNT(*) FROM task_dependencies WHERE plan_id = ?", (plan_id,)
            ).fetchone()[0]


def _draft(row: sqlite3.Row) -> DraftRecord:
    return DraftRecord(
        plan_id=row["plan_id"],
        version=row["version"],
        status=row["status"],
        document=DependencyDocument.from_dict(json.loads(row["document"])),
        repository_id=row["repository_id"],
        revision=row["revision"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")

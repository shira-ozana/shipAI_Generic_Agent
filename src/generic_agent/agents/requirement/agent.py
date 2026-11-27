"""``RequirementAgent``: pre-flight in code, then the instruction-driven flow on ``GenericAgent``.

Human-in-the-loop works through turns on one thread:

    agent = RequirementAgent(settings)
    turn = agent.start(thread_id, RequirementInputs(...))   # pre-flight, then first model turn
    turn = agent.reply(thread_id, "answers ...")            # same clone, status -> in_progress

The per-thread clone and the credentials live only in this process's memory
(``_sessions``); graph state and checkpoints hold only the conversation messages.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from generic_agent.agents.requirement.gitops import GitError, GitRepo
from generic_agent.agents.requirement.inputs import (
    SOURCE_BRANCH,
    InputError,
    RequirementInputs,
    RequirementSettings,
    Status,
    load_requirement_settings,
    validate_thread_id,
)
from generic_agent.agents.requirement.loader import load_instruction_parts
from generic_agent.agents.requirement.tools import requirement_tools
from generic_agent.agents.requirement.workspace import Workspace, WorkspaceError
from generic_agent.config import Settings
from generic_agent.runtime import AgentDefinition, GenericAgent

logger = logging.getLogger(__name__)

NO_RUN_MESSAGE = "No Requirement Agent run exists for this thread. Start one first."


class PreflightError(RuntimeError):
    """Pre-flight failed; the model was not called. The message is safe for the UI."""

    def __init__(self, message: str, status: str | None = None) -> None:
        super().__init__(message)
        self.status = status


class SessionError(RuntimeError):
    """The thread has no Requirement Agent run (or already has one)."""


@dataclass(frozen=True)
class RequirementTurn:
    thread_id: str
    reply: str
    status: str | None


class RequirementAgent:
    def __init__(
        self,
        settings: Settings,
        requirement_settings: RequirementSettings | None = None,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        instructions_root: Traversable | Path | None = None,
        max_history_messages: int | None = None,
    ) -> None:
        self.config = requirement_settings or load_requirement_settings()
        _validate_glob(self.config.init_output_glob)
        self._prompt, self._skills = load_instruction_parts(instructions_root)
        self._runner = GenericAgent(
            settings,
            tools={},
            checkpointer=checkpointer or InMemorySaver(),
            max_history_messages=max_history_messages,
        )
        self._sessions: dict[str, Workspace] = {}
        self._lock = threading.Lock()

    # -- public ----------------------------------------------------------------

    def has_session(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._sessions

    def workspace(self, thread_id: str) -> Workspace | None:
        with self._lock:
            return self._sessions.get(thread_id)

    def start(self, thread_id: str, inputs: RequirementInputs) -> RequirementTurn:
        """Run pre-flight, then the first model turn. Raises ``PreflightError``."""
        workspace = self.preflight(thread_id, inputs)
        message = (
            "A new Requirement Agent run has started.\n"
            f"feature_id: {inputs.feature_id}\n"
            f"repository: {inputs.repo_url} (branch {SOURCE_BRANCH}, cloned; read it with your tools)\n"
            f"output folder: {workspace.output_rel}\n"
            "Pre-flight passed and status is in_progress.\n\n"
            f"<requirement_text>\n{inputs.requirement_text.strip()}\n</requirement_text>"
        )
        return self._run(thread_id, workspace, message)

    def reply(self, thread_id: str, message: str) -> RequirementTurn:
        """A follow-up user message: answers to a question round, or the reply to an approval request."""
        workspace = self.workspace(thread_id)
        if workspace is None:
            raise SessionError(NO_RUN_MESSAGE)
        if not isinstance(message, str) or not message.strip():
            raise InputError("message must not be blank.")
        status = workspace.read_status()
        if status in (Status.AWAITING_USER, Status.AWAITING_APPROVAL):
            if status == Status.AWAITING_APPROVAL:
                workspace.record_approval_reply()
            workspace.set_status(Status.IN_PROGRESS)
            label = "answers received" if status == Status.AWAITING_USER else "approval response received"
            try:
                workspace.commit_and_push(label)
            except WorkspaceError as exc:
                logger.warning("Requirement agent could not publish status for thread %s: %s", thread_id, exc)
        return self._run(thread_id, workspace, message)

    # -- pre-flight (code, never the model) ------------------------------------

    def preflight(self, thread_id: str, inputs: RequirementInputs) -> Workspace:
        try:
            validate_thread_id(thread_id)
        except InputError as exc:
            raise PreflightError(str(exc)) from None
        if not isinstance(inputs, RequirementInputs):
            raise PreflightError("inputs are missing.")
        if self.has_session(thread_id):
            raise SessionError("A Requirement Agent run already exists for this thread.")

        root = Path(self.config.workdir) / thread_id
        if root.exists():
            raise PreflightError("A working directory for this thread already exists; use a new thread.")
        workspace = Workspace(root=root, feature_id=inputs.feature_id, credentials=inputs.credentials)
        try:
            GitRepo(root, inputs.credentials).clone(inputs.repo_url, SOURCE_BRANCH)
        except GitError as exc:
            raise PreflightError(f"Could not clone branch {SOURCE_BRANCH} of the repository. {exc}", None) from None

        if not self._has_init_output(workspace):
            self._fail(workspace, f"No Init Agent output found (expected files matching '{self.config.init_output_glob}').")

        workspace.set_status(Status.IN_PROGRESS)
        try:
            workspace.commit_and_push("started")
        except WorkspaceError as exc:
            self._fail(workspace, f"Could not push to {SOURCE_BRANCH}. {exc}", publish=False)
        with self._lock:
            self._sessions[thread_id] = workspace
        return workspace

    def _has_init_output(self, workspace: Workspace) -> bool:
        root = workspace.root.resolve()
        own_output = workspace.output_dir.resolve()
        for match in root.glob(self.config.init_output_glob):
            real = match.resolve()
            if (
                real.is_file()
                and real.is_relative_to(root)
                and not real.is_relative_to(own_output)
                and ".git" not in match.relative_to(root).parts
            ):
                return True
        return False

    def _fail(self, workspace: Workspace, message: str, *, publish: bool = True) -> None:
        workspace.set_status(Status.FAILED)
        if publish:
            try:
                workspace.commit_and_push("failed")
            except WorkspaceError as exc:
                message += f" The failed status could not be published: {exc}"
        raise PreflightError(message, Status.FAILED.value)

    # -- model turn --------------------------------------------------------------

    def _run(self, thread_id: str, workspace: Workspace, message: str) -> RequirementTurn:
        definition = AgentDefinition(prompt=self._prompt, skills=self._skills, tools=requirement_tools(workspace))
        result = self._runner.run(
            definition,
            [HumanMessage(content=message)],
            {"configurable": {"thread_id": thread_id}},
        )
        return RequirementTurn(thread_id, result["messages"][-1].text.strip(), workspace.read_status())


def _validate_glob(pattern: str) -> None:
    pure = PurePosixPath(pattern)
    if not pattern or pure.is_absolute() or ".." in pure.parts:
        raise ValueError("the Init-output glob must be a relative pattern without '..'.")

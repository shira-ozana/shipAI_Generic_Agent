"""Offline tests for the Requirement Agent (mocked Cursor SDK, local bare git remote)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from cursor_sdk import CustomTool
from fakes import SECRET
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from requirement_fixtures import GIT_TOKEN, call, git, make_remote, remote_file, remote_status

from generic_agent import GenericAgent
from generic_agent.agents.requirement import (
    GitCredentials,
    InputError,
    PreflightError,
    RequirementAgent,
    RequirementInputs,
    RequirementSettings,
    SessionError,
    load_instruction_parts,
    load_requirement_definition,
)
from generic_agent.agents.requirement import gitops
from generic_agent.agents.requirement.cli import run_requirement_session
from generic_agent.agents.requirement.tools import TOOL_NAMES, requirement_tools
from generic_agent.agents.requirement.workspace import PathError, Workspace, WorkspaceError
from generic_agent.api import create_app
from generic_agent.runtime import AgentDefinition

SKILLS = [
    "clarification-questions",
    "gap-detection",
    "repository-research",
    "requirement-approval",
    "requirement-definition",
    "requirement-publishing",
    "requirement-understanding",
    "requirement-validation",
]
TEMPLATES = ["clarifications-template.md", "requirement-template.md", "status-template.json"]
FEATURE = "ORD-42"
OUT = f".platform/{FEATURE}/requierment"


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    return make_remote(tmp_path)


@pytest.fixture
def req_settings(tmp_path: Path) -> RequirementSettings:
    return RequirementSettings(workdir=tmp_path / "work")


def inputs(remote: Path, **overrides) -> RequirementInputs:
    values = dict(
        repo_url=str(remote),
        feature_id=FEATURE,
        requirement_text="Customers should be able to cancel an order before it ships.",
        credentials=GitCredentials(username="bot", token=SecretStr(GIT_TOKEN)),
    )
    values.update(overrides)
    return RequirementInputs(**values)


def make_agent(settings, req_settings, saver=None) -> RequirementAgent:
    return RequirementAgent(settings, req_settings, checkpointer=saver or InMemorySaver())


def question_round(prompt, options) -> str:
    """Fake model turn: asks one round of questions through the tools."""
    call(options, "write_output", file="clarifications.md",
         content="# Clarifications — ORD-42\n\n## Round 1\n\n### Q1. Paid orders too?\n- Answer: pending")
    call(options, "set_status", status="awaiting_user")
    call(options, "publish_outputs", label="round 1 questions")
    return "Q1. Can paid orders be cancelled too?"


REQUIREMENTS = "# Requirements — ORD-42\n\n## 1. Cancel\nThe system shall allow ...\nSource: Q1\n\n## Out of scope\n- none"


def approval_round(prompt, options, content: str = REQUIREMENTS) -> str:
    """Fake model turn: validated requirements sent for approval."""
    call(options, "write_output", file="requirement.md", content=content)
    call(options, "set_status", status="awaiting_approval")
    call(options, "publish_outputs", label="approval requested")
    return "1. Cancel: The system shall allow ... Do you approve?"


def approve_round(prompt, options) -> str:
    """Fake model turn: the user approved; finish."""
    call(options, "set_status", status="completed")
    call(options, "publish_outputs", label="completed")
    return "Requirements published."


def full_run():
    """Responder for: questions -> answers -> approval request -> approval."""
    turns = iter([question_round, approval_round, approve_round])
    return lambda p, o: next(turns, lambda *_: "generic reply")(p, o)


# --- loader ------------------------------------------------------------------


def test_loader_includes_main_file_all_skills_and_templates_in_stable_order() -> None:
    prompt, skills = load_instruction_parts()
    assert prompt.startswith("# Requirement Agent")
    assert [s.name for s in skills] == SKILLS + [f"template:{t}" for t in TEMPLATES]
    for name in SKILLS:
        content = next(s.content for s in skills if s.name == name)
        assert content.startswith(f"---\nname: {name}\ndescription: ")
        for section in ("## Purpose", "## When", "## Steps", "## Output", "## Rules"):
            assert section in content, (name, section)
    definition = load_requirement_definition()
    assert definition.prompt == prompt and definition.skills == skills


def test_loader_can_point_at_another_folder(tmp_path: Path) -> None:
    (tmp_path / "skills" / "zeta").mkdir(parents=True)
    (tmp_path / "skills" / "alpha").mkdir()
    (tmp_path / "skills" / "zeta" / "SKILL.md").write_text("Z", encoding="utf-8")
    (tmp_path / "skills" / "alpha" / "SKILL.md").write_text("A", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("MAIN", encoding="utf-8")
    prompt, skills = load_instruction_parts(tmp_path)
    assert prompt == "MAIN" and [s.name for s in skills] == ["alpha", "zeta"]


def test_all_instructions_reach_the_model(mock_cursor, settings, remote, req_settings) -> None:
    cursor = mock_cursor(question_round)
    make_agent(settings, req_settings).start("t1", inputs(remote))
    prompt = cursor.prompts[0]
    for name in SKILLS + [f"template:{t}" for t in TEMPLATES]:
        assert f'<skill name="{name}">' in prompt
    assert "Customers should be able to cancel" in prompt
    options = cursor.calls[0][1]
    assert set(options.local.custom_tools) == set(TOOL_NAMES)
    assert options.tools == []  # still no Cursor shell/file tools


# --- runtime extension -------------------------------------------------------


def test_definition_tools_replace_runner_tools_for_that_run_only(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "ok")
    mine = CustomTool(execute=lambda a, c: "x", description="d", input_schema={"type": "object"})
    runner = GenericAgent(settings=settings, tools={"base": mine})
    runner.run(AgentDefinition(prompt="p", tools={"own": mine}), [HumanMessage(content="hi")])
    runner.run(AgentDefinition(prompt="p"), [HumanMessage(content="hi")])
    runner.run(AgentDefinition(prompt="p", tools={}), [HumanMessage(content="hi")])
    assert set(cursor.calls[0][1].local.custom_tools) == {"own"}
    assert set(cursor.calls[1][1].local.custom_tools) == {"base"}
    assert cursor.calls[2][1].local.custom_tools is None
    with pytest.raises(TypeError):
        AgentDefinition(prompt="p", tools={"bad": object()})


# --- pre-flight --------------------------------------------------------------


@pytest.mark.parametrize("field", ["repo_url", "feature_id", "requirement_text"])
def test_missing_input_is_rejected(remote, field) -> None:
    with pytest.raises(InputError, match=field):
        inputs(remote, **{field: "  "})


@pytest.mark.parametrize("feature_id", ["../x", "a/b", "a\\b", ".hidden", "..", "a..b", "x.", "-x", "a b", "x" * 101])
def test_unsafe_feature_id_is_rejected(remote, feature_id) -> None:
    with pytest.raises(InputError, match="feature_id"):
        inputs(remote, feature_id=feature_id)


def test_repo_url_with_embedded_credentials_is_rejected() -> None:
    with pytest.raises(InputError, match="credentials"):
        inputs(Path("x"), repo_url=f"https://bot:{GIT_TOKEN}@example.com/r.git")


def test_clone_failure_makes_no_model_call(mock_cursor, settings, tmp_path, req_settings) -> None:
    cursor = mock_cursor(question_round)
    agent = make_agent(settings, req_settings)
    with pytest.raises(PreflightError, match="Could not clone branch ShipAi") as info:
        agent.start("t1", inputs(tmp_path / "missing.git"))
    assert cursor.calls == [] and info.value.status is None
    assert GIT_TOKEN not in str(info.value)
    assert not agent.has_session("t1")


def test_missing_shipai_branch_is_a_clone_failure(mock_cursor, settings, tmp_path, req_settings) -> None:
    cursor = mock_cursor(question_round)
    other = make_remote(tmp_path, branch="main")
    with pytest.raises(PreflightError, match="ShipAi"):
        make_agent(settings, req_settings).start("t1", inputs(other))
    assert cursor.calls == []


def test_no_init_output_publishes_failed_and_makes_no_model_call(mock_cursor, settings, tmp_path, req_settings) -> None:
    cursor = mock_cursor(question_round)
    bare = make_remote(tmp_path, with_init=False)
    with pytest.raises(PreflightError, match=r"No Init Agent output.*\.platform/\*\*/\*\.md") as info:
        make_agent(settings, req_settings).start("t1", inputs(bare))
    assert cursor.calls == [] and info.value.status == "failed"
    assert remote_status(bare, FEATURE) == "failed"


def test_init_check_ignores_this_features_own_output(mock_cursor, settings, tmp_path, req_settings) -> None:
    seed_remote = make_remote(tmp_path, with_init=False)
    clone = tmp_path / "c"
    git("clone", "-q", "-b", "ShipAi", str(seed_remote), str(clone), cwd=tmp_path)
    (clone / OUT).mkdir(parents=True)
    (clone / OUT / "requirement.md").write_text("old", encoding="utf-8")
    git("add", "-A", cwd=clone); git("commit", "-q", "-m", "old run", cwd=clone); git("push", "-q", cwd=clone)
    mock_cursor(question_round)
    with pytest.raises(PreflightError, match="No Init Agent output"):
        make_agent(settings, req_settings).start("t1", inputs(seed_remote))


def test_init_check_is_a_single_setting(mock_cursor, settings, remote, tmp_path) -> None:
    mock_cursor(question_round)
    strict = RequirementSettings(workdir=tmp_path / "w", init_output_glob=".platform/architecture.md")
    with pytest.raises(PreflightError, match="architecture.md"):
        make_agent(settings, strict).start("t1", inputs(remote))
    with pytest.raises(ValueError):
        make_agent(settings, RequirementSettings(workdir=tmp_path / "w2", init_output_glob="../*.md"))


def test_start_sets_in_progress_before_the_model_runs(mock_cursor, settings, remote, req_settings) -> None:
    seen = {}

    def responder(prompt, options):
        seen["status"] = remote_status(remote, FEATURE)
        return "Working on it."

    mock_cursor(responder)
    turn = make_agent(settings, req_settings).start("t1", inputs(remote))
    assert seen["status"] == "in_progress" and turn.status == "in_progress"
    log = git("log", "--format=%s", "ShipAi", cwd=remote)
    assert f"requirement-agent({FEATURE}): started" in log


def test_a_thread_cannot_be_started_twice(mock_cursor, settings, remote, req_settings) -> None:
    mock_cursor(question_round)
    agent = make_agent(settings, req_settings)
    agent.start("t1", inputs(remote))
    with pytest.raises(SessionError):
        agent.start("t1", inputs(remote))
    with pytest.raises(SessionError):
        agent.reply("unknown", "answers")


# --- tools -------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path, remote) -> Workspace:
    root = tmp_path / "clone"
    gitops.GitRepo(root).clone(str(remote))
    return Workspace(root=root, feature_id=FEATURE, credentials=GitCredentials())


def test_read_tools_work_inside_the_clone(workspace) -> None:
    tools = requirement_tools(workspace)
    ctx = None
    assert "overview.md" in tools["list_files"].execute({"path": ".platform"}, ctx)
    assert ".git" not in tools["list_files"].execute({}, ctx)
    assert "src/orders.py" in tools["list_files"].execute({"recursive": True}, ctx)
    assert "placed, paid" in tools["read_file"].execute({"path": ".platform/overview.md"}, ctx)
    assert tools["read_file"].execute({"path": "src/orders.py", "start_line": 1, "max_lines": 1}, ctx).startswith("STATES")
    assert "src/orders.py:1:" in tools["search_files"].execute({"query": "SHIPPED"}, ctx)


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "C:/Windows/win.ini", "C:\\Windows", "//server/share", "../outside.txt", "src/../../x", ".git/config", ".git"]
)
def test_read_tools_reject_paths_outside_the_clone(workspace, path) -> None:
    for method in (workspace.read_file, workspace.list_files, lambda p: workspace.search_files("x", p)):
        with pytest.raises(PathError):
            method(path)


def test_read_tools_reject_symlinks_that_escape(workspace, tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    try:
        os.symlink(secret, workspace.root / "link.txt")
        os.symlink(tmp_path, workspace.root / "linkdir", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this system")
    with pytest.raises(PathError):
        workspace.read_file("link.txt")
    with pytest.raises(PathError):
        workspace.list_files("linkdir")
    assert "TOP-SECRET" not in workspace.search_files("TOP-SECRET")
    assert "link.txt" not in workspace.list_files(".")


@pytest.mark.parametrize("name", ["status.json", "notes.md", "../requirement.md", "x/requirement.md", "REQUIREMENT.md", ""])
def test_write_tool_rejects_other_names(workspace, name) -> None:
    with pytest.raises(PathError):
        workspace.write_output(name, "content")


def test_write_tool_writes_only_into_the_output_folder(workspace) -> None:
    workspace.write_output("clarifications.md", "# Clarifications")
    assert (workspace.root / OUT / "clarifications.md").read_text(encoding="utf-8") == "# Clarifications\n"
    changed = git("status", "--porcelain", "--untracked-files=all", cwd=workspace.root).split()
    assert changed[1:] == [f"{OUT}/clarifications.md"]


@pytest.mark.parametrize("status", ["done", "IN_PROGRESS", "", "awaiting"])
def test_set_status_rejects_invalid_values(workspace, status) -> None:
    with pytest.raises(ValueError, match="status must be one of"):
        workspace.set_status(status)


def test_status_json_holds_only_the_status(workspace) -> None:
    for status in ("in_progress", "awaiting_user", "failed"):
        workspace.set_status(status)
        assert json.loads((workspace.root / OUT / "status.json").read_text(encoding="utf-8")) == {"status": status}
    for status in ("awaiting_approval", "completed"):
        with pytest.raises(ValueError, match="requirement.md"):
            workspace.set_status(status)
    workspace.write_output("requirement.md", "# Requirements")
    workspace.set_status("awaiting_approval")
    workspace.record_approval_reply()
    workspace.set_status("completed")
    assert json.loads((workspace.root / OUT / "status.json").read_text(encoding="utf-8")) == {"status": "completed"}


# --- approval guard ----------------------------------------------------------


def test_completed_requires_an_approval_request_and_a_reply(workspace) -> None:
    workspace.write_output("requirement.md", "# Requirements")
    with pytest.raises(ValueError, match="approval"):
        workspace.set_status("completed")  # never asked
    workspace.set_status("awaiting_approval")
    with pytest.raises(ValueError, match="approval"):
        workspace.set_status("completed")  # asked, but the user has not replied
    workspace.record_approval_reply()
    workspace.set_status("completed")


def test_changed_requirements_need_a_new_approval(workspace) -> None:
    workspace.write_output("requirement.md", "# Requirements v1")
    workspace.set_status("awaiting_approval")
    workspace.record_approval_reply()
    workspace.write_output("requirement.md", "# Requirements v2")
    with pytest.raises(ValueError, match="changed after the approval request"):
        workspace.set_status("completed")
    workspace.set_status("awaiting_approval")  # a new request resets the reply
    with pytest.raises(ValueError, match="approval"):
        workspace.set_status("completed")
    workspace.record_approval_reply()
    workspace.set_status("completed")


# --- publishing --------------------------------------------------------------


def test_publish_commits_only_the_output_folder_and_pushes_to_shipai(workspace, remote) -> None:
    (workspace.root / "src" / "orders.py").write_text("tampered\n", encoding="utf-8")
    (workspace.root / "stray.txt").write_text("stray\n", encoding="utf-8")
    workspace.write_output("clarifications.md", "# Clarifications")
    workspace.set_status("awaiting_user")

    result = workspace.publish("round 1 questions")

    assert "pushed to ShipAi" in result
    subject = git("log", "-1", "--format=%s", "ShipAi", cwd=remote).strip()
    assert subject == f"requirement-agent({FEATURE}): round 1 questions"
    files = git("show", "--name-only", "--format=", "ShipAi", cwd=remote).split()
    assert sorted(files) == [f"{OUT}/clarifications.md", f"{OUT}/status.json"]
    assert remote_file(remote, "src/orders.py").startswith("STATES")


@pytest.mark.parametrize("label", ["done", "round 0 questions", "completed; rm -rf", "round 1 questions\n", ""])
def test_publish_rejects_other_labels(workspace, label) -> None:
    with pytest.raises(ValueError, match="label"):
        workspace.publish(label)


def test_publish_retries_once_after_rejection_and_never_forces(workspace, remote, tmp_path, monkeypatch) -> None:
    other = tmp_path / "other"
    git("clone", "-q", "-b", "ShipAi", str(remote), str(other), cwd=tmp_path)
    (other / "NEWS.md").write_text("someone else\n", encoding="utf-8")
    git("add", "-A", cwd=other); git("commit", "-q", "-m", "concurrent", cwd=other); git("push", "-q", cwd=other)

    commands: list[tuple[str, ...]] = []
    original = gitops.GitRepo.run

    def recording(self, *args, **kwargs):
        commands.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(gitops.GitRepo, "run", recording)
    workspace.set_status("awaiting_user")
    assert "after one rebase retry" in workspace.publish("round 1 questions")

    log = git("log", "--format=%s", "ShipAi", cwd=remote).splitlines()
    assert log[:2] == [f"requirement-agent({FEATURE}): round 1 questions", "concurrent"]
    pushes = [c for c in commands if c[0] == "push"]
    assert len(pushes) == 2
    assert not any("--force" in a or a.startswith("+") or a == "-f" for c in commands for a in c)


def test_publish_gives_up_after_one_retry(workspace, monkeypatch) -> None:
    pushes = []
    original = gitops.GitRepo.run

    def failing_push(self, *args, check=True, **kwargs):
        if args[0] == "push":
            pushes.append(args)
            proc = subprocess.CompletedProcess(args, 1, "", "rejected")
            if check:
                raise gitops.GitError("git push failed: rejected")
            return proc
        if args[0] == "pull":
            return subprocess.CompletedProcess(args, 0, "", "")
        return original(self, *args, check=check, **kwargs)

    monkeypatch.setattr(gitops.GitRepo, "run", failing_push)
    workspace.set_status("failed")
    with pytest.raises(WorkspaceError, match="publishing failed"):
        workspace.publish("failed")
    assert len(pushes) == 2


# --- human in the loop -------------------------------------------------------


def test_questions_answers_approval_then_completed_on_the_same_thread(
    mock_cursor, settings, remote, req_settings
) -> None:
    steps = [question_round, approval_round, approve_round]
    statuses: list[str] = []

    def responder(prompt, options):
        call(options, "list_files", path=".")
        statuses.append(remote_status(remote, FEATURE))
        return steps[len(statuses) - 1](prompt, options)

    cursor = mock_cursor(responder)
    agent = make_agent(settings, req_settings)

    first = agent.start("t1", inputs(remote))
    assert first.status == "awaiting_user" and remote_status(remote, FEATURE) == "awaiting_user"
    clone = agent.workspace("t1").root

    second = agent.reply("t1", "Yes, paid orders too, with a full refund.")
    assert second.status == "awaiting_approval" and remote_status(remote, FEATURE) == "awaiting_approval"
    assert "Do you approve?" in second.reply
    assert remote_file(remote, f"{OUT}/requirement.md").startswith("# Requirements")

    third = agent.reply("t1", "Approved.")
    assert agent.workspace("t1").root == clone  # same clone reused throughout
    assert statuses == ["in_progress"] * 3  # each user reply sets in_progress before the model runs
    assert third.status == "completed" and remote_status(remote, FEATURE) == "completed"
    assert "Customers should be able to cancel" in cursor.prompts[2]  # history from the checkpoint
    assert "Approved." in cursor.prompts[2].split("<current_user_message>")[1]
    log = git("log", "--format=%s", "ShipAi", cwd=remote).splitlines()
    assert log[:6] == [
        f"requirement-agent({FEATURE}): completed",
        f"requirement-agent({FEATURE}): approval response received",
        f"requirement-agent({FEATURE}): approval requested",
        f"requirement-agent({FEATURE}): answers received",
        f"requirement-agent({FEATURE}): round 1 questions",
        f"requirement-agent({FEATURE}): started",
    ]


def test_requested_changes_lead_to_a_new_approval_round(mock_cursor, settings, remote, req_settings) -> None:
    def premature_completion(prompt, options):
        call(options, "write_output", file="requirement.md", content=REQUIREMENTS + "\n- partial orders")
        with pytest.raises(ValueError, match="changed after the approval request"):
            call(options, "set_status", status="completed")  # code refuses: the user never saw this version
        return approval_round(prompt, options, REQUIREMENTS + "\n- partial orders")

    steps = iter([approval_round, premature_completion, approve_round])
    mock_cursor(lambda p, o: next(steps)(p, o))
    agent = make_agent(settings, req_settings)

    assert agent.start("t1", inputs(remote)).status == "awaiting_approval"
    assert agent.reply("t1", "Exclude partial orders.").status == "awaiting_approval"
    assert agent.reply("t1", "Approved.").status == "completed"
    assert "partial orders" in remote_file(remote, f"{OUT}/requirement.md")


def test_model_cannot_complete_without_asking_for_approval(mock_cursor, settings, remote, req_settings) -> None:
    def skip_approval(prompt, options):
        call(options, "write_output", file="requirement.md", content=REQUIREMENTS)
        with pytest.raises(ValueError, match="approval"):
            call(options, "set_status", status="completed")
        return "skipped"

    mock_cursor(skip_approval)
    turn = make_agent(settings, req_settings).start("t1", inputs(remote))
    assert turn.status == "in_progress"


# --- credentials -------------------------------------------------------------


def test_credentials_never_leak(mock_cursor, settings, remote, req_settings, caplog, monkeypatch) -> None:
    caplog.set_level(logging.DEBUG)
    tool_outputs: list[str] = []

    def responder(prompt, options):
        for name, args in [("list_files", {"recursive": True}), ("search_files", {"query": "a"})]:
            tool_outputs.append(call(options, name, **args))
        return question_round(prompt, options)

    git_calls: list[tuple[list[str], dict]] = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git" and "GIT_TERMINAL_PROMPT" in (kwargs.get("env") or {}):  # agent's git only
            git_calls.append((list(cmd), dict(kwargs.get("env") or {})))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(gitops.subprocess, "run", spy)
    cursor = mock_cursor(responder)
    saver = InMemorySaver()
    agent = make_agent(settings, req_settings, saver)
    req = inputs(remote)
    agent.start("t1", req)
    agent.reply("t1", "answers")

    root = agent.workspace("t1").root
    haystacks = {
        "prompts": "\n".join(cursor.prompts),
        "checkpoints": repr(saver.storage),
        "tool output": "\n".join(tool_outputs),
        "logs": caplog.text,
        "repr": repr(req) + repr(agent.workspace("t1")),
        ".git/config": (root / ".git" / "config").read_text(encoding="utf-8"),
        "remote log": git("log", "--format=%B", "ShipAi", cwd=remote),
        "outputs": "".join(p.read_text(encoding="utf-8") for p in (root / OUT).iterdir()),
    }
    for where, text in haystacks.items():
        assert GIT_TOKEN not in text, where
        assert SECRET not in text, where
    assert git_calls, "git was not called"
    for argv, env in git_calls:
        assert not any(GIT_TOKEN in a for a in argv)  # never on the command line
        assert env.get("GENERIC_AGENT_GIT_TOKEN") == GIT_TOKEN  # only in the child environment
    assert "[credential" not in haystacks[".git/config"] and "helper" not in haystacks[".git/config"]


def test_credential_helper_supplies_the_token_to_git_offline(tmp_path) -> None:
    """``git credential fill`` asks the same helper an HTTPS push would use (no network)."""
    repo = gitops.GitRepo(tmp_path, GitCredentials(username="bot", token=SecretStr(GIT_TOKEN)))
    proc = subprocess.run(
        [*repo._base_args(), "credential", "fill"],
        input="protocol=https\nhost=example.com\n\n",
        cwd=str(tmp_path), env=repo._env(), capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "username=bot" in proc.stdout and f"password={GIT_TOKEN}" in proc.stdout


def test_git_errors_are_scrubbed(tmp_path) -> None:
    repo = gitops.GitRepo(tmp_path / "x", GitCredentials(token=SecretStr(GIT_TOKEN)))
    with pytest.raises(gitops.GitError) as info:
        repo.clone(f"{tmp_path}/no-such-{GIT_TOKEN}.git")
    assert GIT_TOKEN not in str(info.value)


# --- HTTP API ----------------------------------------------------------------


@pytest.fixture
def api(settings, req_settings, mock_cursor):
    def make(responder):
        fake = mock_cursor(responder)
        client = TestClient(create_app(settings, requirement_settings=req_settings))
        client.__enter__()
        return client, fake

    clients = []
    yield lambda responder: clients.append(make(responder)) or clients[-1]
    for client, _ in clients:
        client.__exit__(None, None, None)


def start_body(remote, thread_id, **overrides):
    body = {
        "threadId": thread_id,
        "repoUrl": str(remote),
        "featureId": FEATURE,
        "requirementText": "Customers cancel orders before shipping.",
        "credentials": {"username": "bot", "token": GIT_TOKEN},
    }
    body.update(overrides)
    return body


def test_api_start_then_answer(api, remote) -> None:
    client, fake = api(full_run())
    thread_id = str(uuid.uuid4())

    first = client.post("/api/requirement/start", json=start_body(remote, thread_id))
    assert first.status_code == 200, first.text
    assert first.json() == {"threadId": thread_id, "reply": "Q1. Can paid orders be cancelled too?", "status": "awaiting_user"}
    second = client.post("/api/requirement/chat", json={"threadId": thread_id, "message": "Yes"})
    assert second.status_code == 200 and second.json()["status"] == "awaiting_approval"
    third = client.post("/api/requirement/chat", json={"threadId": thread_id, "message": "Approved"})
    assert third.status_code == 200 and third.json()["status"] == "completed"
    assert GIT_TOKEN not in first.text + second.text + third.text
    # The generic chat endpoint is unchanged and separate.
    assert client.post("/api/chat", json={"threadId": str(uuid.uuid4()), "message": "hi"}).status_code == 200


def test_api_errors(api, remote, tmp_path) -> None:
    client, fake = api(question_round)
    tid = lambda: str(uuid.uuid4())  # noqa: E731

    bad_feature = client.post("/api/requirement/start", json=start_body(remote, tid(), featureId="../x"))
    assert bad_feature.status_code == 422 and "feature_id" in bad_feature.json()["detail"]
    missing = client.post("/api/requirement/start", json={"threadId": tid(), "credentials": {"token": GIT_TOKEN}})
    assert missing.status_code == 422 and GIT_TOKEN not in missing.text  # validation errors never echo input
    clone_fail = client.post("/api/requirement/start", json=start_body(tmp_path / "nope.git", tid()))
    assert clone_fail.status_code == 400 and "Could not clone" in clone_fail.json()["detail"]
    assert GIT_TOKEN not in clone_fail.text
    no_run = client.post("/api/requirement/chat", json={"threadId": tid(), "message": "hi"})
    assert no_run.status_code == 404
    assert fake.calls == []


# --- CLI ---------------------------------------------------------------------


def test_cli_session_runs_rounds_until_exit(mock_cursor, settings, remote, req_settings) -> None:
    mock_cursor(full_run())
    lines = iter(["Yes, refund in full", "Approved", "exit"])
    out: list[str] = []
    code = run_requirement_session(make_agent(settings, req_settings), inputs(remote),
                                   input_fn=lambda _: next(lines), output_fn=out.append)
    assert code == 0
    assert out == [
        "agent> Q1. Can paid orders be cancelled too?", "[status: awaiting_user]",
        "agent> 1. Cancel: The system shall allow ... Do you approve?", "[status: awaiting_approval]",
        "agent> Requirements published.", "[status: completed]",
    ]


def test_cli_reports_preflight_failure(mock_cursor, settings, tmp_path, req_settings) -> None:
    cursor = mock_cursor(question_round)
    out: list[str] = []
    code = run_requirement_session(make_agent(settings, req_settings), inputs(tmp_path / "nope.git"),
                                   input_fn=lambda _: "exit", output_fn=out.append)
    assert code == 1 and out[0].startswith("error: Could not clone") and cursor.calls == []


def test_main_delegates_to_the_requirement_cli(monkeypatch) -> None:
    from generic_agent import __main__ as cli

    seen = {}
    monkeypatch.setattr("generic_agent.agents.requirement.cli.main", lambda argv: seen.setdefault("argv", argv) and 7)
    assert cli.main(["requirement", "--feature-id", "x"]) == 7
    assert seen["argv"] == ["--feature-id", "x"]

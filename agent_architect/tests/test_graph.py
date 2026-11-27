import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_architect.graph import PrerequisiteError, build_architect_graph
from conftest import FEATURE_ID, REPO_URL, FakeLlm, analysis_json, design_json, read_remote, seed_remote


def _graph(remote: Path, llm, tmp_path: Path, **kwargs):
    return build_architect_graph(
        llm=llm,
        credentials=lambda u, r: None,
        model_name="fake-model",
        clone_url=lambda url: str(remote),
        workdir_root=tmp_path,
        checkpointer=InMemorySaver(),
        **kwargs,
    )


INPUT = {"username": "shira", "repo_url": REPO_URL, "feature_id": FEATURE_ID}
CFG = {"configurable": {"thread_id": "t1"}}


def _interrupt(graph):
    return graph.get_state(CFG).interrupts[0].value


def test_full_flow_clarify_feedback_approve(tmp_path, remote):
    llm = FakeLlm(
        analysis_json(),
        design_json(("backend/api",), "# Draft 1"),
        design_json(("backend/api", "frontend/web"), "# Draft 2"),
    )
    graph = _graph(remote, llm, tmp_path)

    # HITL 1: requirement analysis with clarifying questions
    graph.invoke(INPUT, CFG)
    payload = _interrupt(graph)
    assert payload["stage"] == "clarify"
    assert payload["questions"] == ["Which email provider?"]
    assert payload["requirement_path"] == f".platform/{FEATURE_ID}/requierment/requirement.md"
    assert "<step>1 - requirement analysis</step>" in llm.messages[0]
    assert "Users can reset their password." in llm.messages[0]
    assert "Registered users only." in llm.messages[0], "clarifications.md is part of the context"

    # HITL 2: first draft uses the analysis and the user's answers
    graph.invoke(Command(resume={"answers": "Use SendGrid."}), CFG)
    payload = _interrupt(graph)
    assert payload["stage"] == "review" and payload["revision"] == 1
    assert "<step>2 - HLD design</step>" in llm.messages[1]
    assert "Use SendGrid." in llm.messages[1] and "FR-1: A user can request a reset link." in llm.messages[1]

    graph.invoke(Command(resume={"approved": False, "feedback": "Add the frontend component"}), CFG)
    payload = _interrupt(graph)
    assert payload["revision"] == 2
    assert "Add the frontend component" in llm.messages[2] and "# Draft 1" in llm.messages[2]
    assert "Use SendGrid." in llm.messages[2], "clarifications are kept for revisions"

    graph.invoke(Command(resume={"approved": True}), CFG)
    final = graph.get_state(CFG).values
    assert final["result"] == "completed" and final["commit_sha"]
    assert not Path(llm.repo_dirs[0]).exists(), "temporary clone is removed"

    pushed = read_remote(tmp_path, remote)  # ShipAi branch
    assert not (read_remote(tmp_path, remote, "main") / ".platform").exists(), "main is untouched"
    arch = pushed / ".platform" / FEATURE_ID / "architect"
    assert (arch / "HLD.md").read_text() == "# Draft 2\n"
    assert (arch / "components" / "backend" / "api" / "HLD.md").read_text() == "# backend/api HLD\n"
    assert (arch / "components" / "frontend" / "web" / "HLD.md").is_file()
    status = json.loads((arch / "status.json").read_text())
    assert status["status"] == "completed" and status["feature_id"] == FEATURE_ID
    assert status["revision"] == 2 and status["model"] == "fake-model"
    assert status["requirement"] == f".platform/{FEATURE_ID}/requierment/requirement.md"
    assert [c["architecture_path"] for c in status["components"]] == ["backend/api", "frontend/web"]


def test_rejection_pushes_nothing(tmp_path, remote):
    graph = _graph(remote, FakeLlm(analysis_json(), design_json()), tmp_path)
    graph.invoke(INPUT, CFG)
    graph.invoke(Command(resume={"answers": ""}), CFG)
    graph.invoke(Command(resume={"approved": False}), CFG)
    assert graph.get_state(CFG).values["result"] == "rejected"
    assert not (read_remote(tmp_path, remote) / ".platform" / FEATURE_ID / "architect").exists()


def test_empty_answers_keep_assumptions(tmp_path, remote):
    llm = FakeLlm(analysis_json(()), design_json())
    graph = _graph(remote, llm, tmp_path)
    graph.invoke(INPUT, CFG)
    assert _interrupt(graph)["questions"] == []
    graph.invoke(Command(resume={"answers": ""}), CFG)
    assert "use your assumptions" in llm.messages[1]


def test_auto_approve_and_target_branch(tmp_path, remote):
    graph = _graph(
        remote, FakeLlm(analysis_json(), design_json()), tmp_path, require_approval=False, target_branch="platform/arch"
    )
    graph.invoke(INPUT, CFG)
    assert graph.get_state(CFG).values["result"] == "completed"
    assert (read_remote(tmp_path, remote, "platform/arch") / ".platform" / FEATURE_ID / "architect" / "HLD.md").is_file()


def test_invalid_reply_gets_one_corrective_retry(tmp_path, remote):
    llm = FakeLlm(analysis_json(), "sorry, no json", design_json())
    graph = _graph(remote, llm, tmp_path, require_approval=False)
    graph.invoke(INPUT, CFG)
    assert len(llm.messages) == 3 and "was rejected" in llm.messages[2]
    assert graph.get_state(CFG).values["result"] == "completed"


@pytest.mark.parametrize(
    "folder, file_name",
    [("requierment", "requirement.md"), ("requierment", "requierment.md"), ("requirement", "Requirement.md")],
)
def test_finds_requirement_under_both_spellings(tmp_path, folder, file_name):
    remote = seed_remote(tmp_path, folder=folder, file_name=file_name)
    graph = _graph(remote, FakeLlm(analysis_json(), design_json()), tmp_path, require_approval=False)
    graph.invoke(INPUT, CFG)
    assert graph.get_state(CFG).values["requirement_path"] == f".platform/{FEATURE_ID}/{folder}/{file_name}"


@pytest.mark.parametrize("status, with_req", [(None, True), ("in_progress", True), ("completed", False)])
def test_requires_finished_requirement_agent(tmp_path, status, with_req):
    remote = seed_remote(tmp_path, requirement_status=status, with_requirement=with_req)
    graph = _graph(remote, FakeLlm(analysis_json(), design_json()), tmp_path)
    with pytest.raises(PrerequisiteError):
        graph.invoke(INPUT, CFG)


def test_rejects_non_github_url_and_bad_feature_id(tmp_path, remote):
    graph = _graph(remote, FakeLlm(design_json()), tmp_path)
    with pytest.raises(ValueError):
        graph.invoke({**INPUT, "repo_url": "https://example.com/x"}, CFG)
    with pytest.raises(ValueError):
        graph.invoke({**INPUT, "feature_id": "../escape"}, {"configurable": {"thread_id": "t2"}})


def test_feature_id_rule_matches_requirement_agent(tmp_path):
    feature_id = "feature-42_login.v2"
    remote = seed_remote(tmp_path, feature_id=feature_id)
    graph = _graph(remote, FakeLlm(analysis_json(), design_json()), tmp_path, require_approval=False)
    graph.invoke({**INPUT, "feature_id": feature_id}, CFG)
    assert graph.get_state(CFG).values["result"] == "completed"


def test_missing_branch_fails_clearly(tmp_path):
    remote = seed_remote(tmp_path, branch="main")
    graph = _graph(remote, FakeLlm(analysis_json(), design_json()), tmp_path)
    with pytest.raises(Exception, match="ShipAi"):
        graph.invoke(INPUT, CFG)

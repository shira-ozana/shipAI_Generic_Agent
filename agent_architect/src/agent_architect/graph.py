"""Architect Agent graph.

START -> validate_input -> clone_repo -> check_prerequisites
      -> analyze_requirement -> clarify_requirement (HITL 1: answers to questions)
      -> generate_hld -> human_review (HITL 2: approve / feedback / reject)
      -> write_outputs -> commit_and_push -> cleanup -> END
         human_review: feedback -> generate_hld, rejected -> cleanup
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import HttpUrl, TypeAdapter, ValidationError

from agent_architect import git_ops, platform_paths as paths
from agent_architect.credentials import GitHubCredentials
from agent_architect.design import (
    ANALYSIS_SCHEMA,
    DESIGN_SCHEMA,
    ArchitectureDesign,
    DesignParseError,
    RequirementAnalysis,
    parse_analysis,
    parse_design,
)
from agent_architect.llm import LlmRunner
from agent_architect.status import (
    REQUIREMENT_DONE_STATUSES,
    build_architect_status,
    now_iso,
    read_status,
    write_json,
)

CredentialsProvider = Callable[[str, str], GitHubCredentials | None]
"""(username, repo_url) -> GitHub credentials (None for anonymous/local repos)."""


class PrerequisiteError(RuntimeError):
    """The Requirements Agent has not finished for this feature."""


class ArchitectState(TypedDict, total=False):
    # Inputs
    username: str
    repo_url: str
    feature_id: str
    # Working state
    started_at: str
    repo_dir: str
    requirement: str
    requirement_path: str
    requirement_clarifications: str
    init_context: str
    analysis: dict[str, Any]
    clarifications: str
    design: dict[str, Any]
    revision: int
    feedback: str
    approved: bool
    # Outputs
    written_files: list[str]
    commit_sha: str | None
    result: str


def _normalise_repo_url(repo_url: str) -> str:
    """Same normalisation as the backend (pydantic ``HttpUrl``), so DB lookups match."""
    try:
        url = TypeAdapter(HttpUrl).validate_python(repo_url)
    except ValidationError as exc:
        raise ValueError(f"repo_url is not a valid URL: {repo_url!r}") from exc
    if url.host not in ("github.com", "www.github.com"):
        raise ValueError("repo_url must be a github.com URL")
    return str(url)


def _context_sections(state: ArchitectState) -> list[str]:
    sections = [
        f"<feature_id>{state['feature_id']}</feature_id>",
        f"<requirement path=\"{state.get('requirement_path', '')}\">\n{state['requirement']}\n</requirement>",
    ]
    if state.get("requirement_clarifications"):
        sections.append(
            "<requirement_clarifications>\n"
            f"{state['requirement_clarifications']}\n</requirement_clarifications>"
        )
    if state.get("init_context"):
        sections.append(f"<project_documentation>\n{state['init_context']}\n</project_documentation>")
    return sections


def render_analysis_request(state: ArchitectState) -> str:
    return "\n\n".join(
        [
            "<step>1 - requirement analysis</step>",
            *_context_sections(state),
            "Analyse the requirement against the existing repository using the "
            "requirements-analysis and codebase-discovery skills. Put what you need "
            "the user to decide in `questions`, and what you will assume otherwise in "
            "`assumptions`. Reply with only this JSON object:\n" + ANALYSIS_SCHEMA,
        ]
    )


def render_design_request(state: ArchitectState) -> str:
    sections = ["<step>2 - HLD design</step>", *_context_sections(state)]
    if state.get("analysis"):
        analysis = RequirementAnalysis.model_validate(state["analysis"]).model_dump_json(indent=2)
        sections.append(f"<your_requirement_analysis>\n{analysis}\n</your_requirement_analysis>")
    clarifications = state.get("clarifications") or "(no answers; use your assumptions and list them as open questions)"
    sections.append(f"<user_clarifications>\n{clarifications}\n</user_clarifications>")
    if state.get("design") and state.get("feedback"):
        previous = ArchitectureDesign.model_validate(state["design"]).model_dump_json(indent=2)
        sections.append(f"<previous_draft>\n{previous}\n</previous_draft>")
        sections.append(
            f"<reviewer_feedback>\n{state['feedback']}\n</reviewer_feedback>\n"
            "Revise the previous draft according to the reviewer feedback."
        )
    sections.append(
        "Write the overall HLD and one HLD per component, applying all your skills "
        "(include the requirements traceability table). Reply with only this JSON object:\n"
        + DESIGN_SCHEMA
    )
    return "\n\n".join(sections)


def build_architect_graph(
    *,
    llm: LlmRunner,
    credentials: CredentialsProvider,
    model_name: str,
    author_name: str = "SDLC Architect Agent",
    author_email: str = "architect-agent@sdlc.local",
    source_branch: str | None = "ShipAi",
    target_branch: str | None = None,
    require_approval: bool = True,
    clone_url: Callable[[str], str] = lambda url: url,
    workdir_root: Path | None = None,
    checkpointer: BaseCheckpointSaver | bool | None = None,
) -> CompiledStateGraph:
    """Compile the architect graph.

    Args:
        llm: Runs the model (``make_generic_llm`` in production, a fake in tests).
        credentials: Looks up the GitHub credentials for ``(username, repo_url)``.
        model_name: Recorded in ``status.json``.
        source_branch: Branch to clone and push to; the Requirement Agent uses ``ShipAi``.
            ``None`` uses the repository's default branch.
        target_branch: Push here instead of ``source_branch``.
        require_approval: Pause for human review (HITL). Needs a checkpointer.
        clone_url: Maps the registered repo URL to the URL to clone (tests use local repos).
        workdir_root: Parent folder for the temporary clone (system temp by default).
    """

    def validate_input(state: ArchitectState) -> ArchitectState:
        if not state.get("username"):
            raise ValueError("username is required")
        return {
            "feature_id": paths.validate_feature_id(state["feature_id"]),
            "repo_url": _normalise_repo_url(state["repo_url"]),
            "started_at": now_iso(),
            "revision": 0,
        }

    def clone_repo(state: ArchitectState) -> ArchitectState:
        creds = credentials(state["username"], state["repo_url"])
        repo_dir = Path(
            tempfile.mkdtemp(prefix=f"architect-{state['feature_id'][:8]}-", dir=workdir_root)
        )
        try:
            git_ops.clone(clone_url(state["repo_url"]), repo_dir, creds, source_branch)
        except Exception:
            shutil.rmtree(repo_dir, ignore_errors=True)
            raise
        return {"repo_dir": str(repo_dir)}

    def check_prerequisites(state: ArchitectState) -> ArchitectState:
        repo = Path(state["repo_dir"])
        fid = state["feature_id"]
        found = paths.find_requirement_files(repo, fid)
        if found is None:
            tried = ", ".join(f"{d}/" for d in paths.requirement_dir_candidates(fid))
            raise PrerequisiteError(
                f"No requirement file found for feature {fid} (looked in {tried}). "
                "Run the Requirements Agent first."
            )
        requirement, status_file = found
        rel_status = status_file.relative_to(repo).as_posix()
        if not status_file.is_file():
            raise PrerequisiteError(f"{rel_status} not found.")
        req_status = str(read_status(status_file).get("status", "")).lower()
        if req_status not in REQUIREMENT_DONE_STATUSES:
            raise PrerequisiteError(
                f"Requirements Agent status in {rel_status} is {req_status or 'missing'!r}, "
                f"expected one of {sorted(REQUIREMENT_DONE_STATUSES)}."
            )
        init = repo / paths.init_file()
        clarifications = requirement.parent / paths.CLARIFICATIONS_FILE
        return {
            "requirement": requirement.read_text(encoding="utf-8"),
            "requirement_path": requirement.relative_to(repo).as_posix(),
            "requirement_clarifications": (
                clarifications.read_text(encoding="utf-8") if clarifications.is_file() else ""
            ),
            "init_context": init.read_text(encoding="utf-8") if init.is_file() else "",
        }

    def _ask(message: str, repo_dir: str, parse: Callable[[str], Any]) -> Any:
        """Call the LLM and parse its JSON; one corrective retry on an invalid reply."""
        reply = llm(message, Path(repo_dir))
        try:
            return parse(reply)
        except DesignParseError as first_error:
            retry = (
                f"{message}\n\nYour previous reply was rejected: {first_error}\n"
                "Reply again with only the JSON object in the required format."
            )
            return parse(llm(retry, Path(repo_dir)))

    def analyze_requirement(state: ArchitectState) -> ArchitectState:
        analysis = _ask(render_analysis_request(state), state["repo_dir"], parse_analysis)
        return {"analysis": analysis.model_dump()}

    def clarify_requirement(state: ArchitectState) -> ArchitectState:
        if not require_approval:
            return {"clarifications": ""}
        analysis = RequirementAnalysis.model_validate(state["analysis"])
        answer = interrupt(
            {
                "stage": "clarify",
                "feature_id": state["feature_id"],
                "requirement_path": state.get("requirement_path", ""),
                **analysis.model_dump(),
                "resume_with": {"answers": "str: answers to the questions and corrections (may be empty)"},
            }
        )
        answers = str(answer.get("answers") or "").strip() if isinstance(answer, dict) else str(answer or "").strip()
        return {"clarifications": answers}

    def generate_hld(state: ArchitectState) -> ArchitectState:
        design = _ask(render_design_request(state), state["repo_dir"], parse_design)
        return {"design": design.model_dump(), "revision": state.get("revision", 0) + 1, "feedback": ""}

    def human_review(state: ArchitectState) -> ArchitectState:
        if not require_approval:
            return {"approved": True}
        design = ArchitectureDesign.model_validate(state["design"])
        decision = interrupt(
            {
                "stage": "review",
                "feature_id": state["feature_id"],
                "revision": state["revision"],
                "overall_hld": design.overall_hld,
                "components": [
                    {"architecture_path": c.architecture_path, "name": c.name, "hld": c.hld}
                    for c in design.components
                ],
                "resume_with": {"approved": "bool", "feedback": "str (when not approved)"},
            }
        )
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
        feedback = str(decision.get("feedback") or "").strip() if isinstance(decision, dict) else ""
        return {"approved": approved, "feedback": "" if approved else feedback}

    def after_review(state: ArchitectState) -> str:
        if state.get("approved"):
            return "write_outputs"
        return "generate_hld" if state.get("feedback") else "reject"

    def reject(state: ArchitectState) -> ArchitectState:
        return {"result": "rejected"}

    def write_outputs(state: ArchitectState) -> ArchitectState:
        repo = Path(state["repo_dir"])
        fid = state["feature_id"]
        design = ArchitectureDesign.model_validate(state["design"])

        # Replace the previous architect output for this feature, so removed
        # components do not linger.
        shutil.rmtree(repo / paths.architect_dir(fid), ignore_errors=True)

        written = [paths.architect_hld_file(fid)]
        (repo / written[0]).parent.mkdir(parents=True, exist_ok=True)
        (repo / written[0]).write_text(design.overall_hld.rstrip() + "\n", encoding="utf-8")

        components = []
        for component in design.components:
            rel = paths.component_hld_file(fid, component.architecture_path)
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(component.hld.rstrip() + "\n", encoding="utf-8")
            written.append(rel)
            components.append(
                {
                    "architecture_path": component.architecture_path,
                    "name": component.name,
                    "hld": rel.relative_to(paths.architect_dir(fid)).as_posix(),
                }
            )

        status_rel = paths.architect_status_file(fid)
        write_json(
            repo / status_rel,
            build_architect_status(
                feature_id=fid,
                status="completed",
                started_at=state["started_at"],
                model=model_name,
                revision=state["revision"],
                components=components,
                requirement_path=state.get("requirement_path", ""),
            ),
        )
        written.append(status_rel)
        return {"written_files": [p.as_posix() for p in written]}

    def commit_and_push(state: ArchitectState) -> ArchitectState:
        fid = state["feature_id"]
        sha = git_ops.commit_and_push(
            Path(state["repo_dir"]),
            [paths.architect_dir(fid).as_posix()],
            f"architect: HLD for feature {fid}",
            credentials(state["username"], state["repo_url"]),
            author_name,
            author_email,
            target_branch,
        )
        return {"commit_sha": sha, "result": "completed" if sha else "no_changes"}

    def cleanup(state: ArchitectState) -> ArchitectState:
        if state.get("repo_dir"):
            shutil.rmtree(state["repo_dir"], ignore_errors=True)
        return {"repo_dir": ""}

    builder = StateGraph(ArchitectState)
    for name, node in [
        ("validate_input", validate_input),
        ("clone_repo", clone_repo),
        ("check_prerequisites", check_prerequisites),
        ("analyze_requirement", analyze_requirement),
        ("clarify_requirement", clarify_requirement),
        ("generate_hld", generate_hld),
        ("human_review", human_review),
        ("reject", reject),
        ("write_outputs", write_outputs),
        ("commit_and_push", commit_and_push),
        ("cleanup", cleanup),
    ]:
        builder.add_node(name, node)

    builder.add_edge(START, "validate_input")
    builder.add_edge("validate_input", "clone_repo")
    builder.add_edge("clone_repo", "check_prerequisites")
    builder.add_edge("check_prerequisites", "analyze_requirement")
    builder.add_edge("analyze_requirement", "clarify_requirement")
    builder.add_edge("clarify_requirement", "generate_hld")
    builder.add_edge("generate_hld", "human_review")
    builder.add_conditional_edges(
        "human_review",
        after_review,
        {"write_outputs": "write_outputs", "generate_hld": "generate_hld", "reject": "reject"},
    )
    builder.add_edge("reject", "cleanup")
    builder.add_edge("write_outputs", "commit_and_push")
    builder.add_edge("commit_and_push", "cleanup")
    builder.add_edge("cleanup", END)
    return builder.compile(checkpointer=checkpointer)


def discard_clone(state: dict[str, Any]) -> None:
    """Remove a leftover clone after a failed run (nodes raise before ``cleanup``)."""
    if state.get("repo_dir"):
        shutil.rmtree(state["repo_dir"], ignore_errors=True)

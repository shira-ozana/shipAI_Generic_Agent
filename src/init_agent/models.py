"""Pydantic contracts: the request from the UI, the structured LLM outputs, and the run result."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- request (UI -> INIT)

class ProjectRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    project_id: str | None = None
    repo_path: str | None = None          # local working copy; Python reads and writes here
    repo_url: str | None = None           # remote read alternative (needs a matching local copy to write)
    ref: str | None = None
    reference: str | None = None
    working_branch: str | None = None


class SharedSkill(BaseModel):
    """Entry of the shared skill catalog. Later agents open it by id or ref."""
    id: str
    description: str = ""
    ref: str = ""                          # URL, path or catalog id


class ScanScope(BaseModel):
    include: list[str] = Field(default_factory=list)   # glob patterns, repo-relative
    exclude: list[str] = Field(default_factory=list)


class InitInputs(BaseModel):
    model_config = ConfigDict(extra="allow")
    project_brief: str = ""
    documents: list[str] = Field(default_factory=list)          # extra context docs (text)
    scan_scope: ScanScope = Field(default_factory=ScanScope)
    shared_skills: list[SharedSkill] = Field(default_factory=list)
    # Operator-supplied prompt additions (appended to the prompt pack for this run only).
    extra_rules: list[str] = Field(default_factory=list)
    extra_guidelines: list[str] = Field(default_factory=list)
    # Commands the operator explicitly allows INIT to execute for verification. Default: none.
    verify_commands: list[str] = Field(default_factory=list)
    # Fixture support: sample scan tool output from the request.
    mock_repo_scan: dict[str, Any] | None = None
    # Fixture support: known retrieval gaps to force a RAG alert in a demo.
    retrieval_gaps: list[str] = Field(default_factory=list)


class InitRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    stage: Literal["init"] = "init"
    fixture: bool = False
    feature_id: str | None = None
    thread_id: str | None = None           # one Generic Agent threadId for this INIT run (optional)
    project: ProjectRef = Field(default_factory=ProjectRef)
    inputs: InitInputs = Field(default_factory=InitInputs)
    seed_files: list[str] = Field(default_factory=list)
    dry_run: bool = False                  # plan only, write nothing


# --------------------------------------------------------------------------- LLM structured outputs

class Finding(BaseModel):
    statement: str
    evidence: list[str] = Field(default_factory=list)    # repo paths or code identifiers
    inferred: bool = False


class CommandInfo(BaseModel):
    command: str
    purpose: str = ""
    source: str = ""                                     # where it is documented
    status: Literal["documented", "executed_passed", "executed_failed"] = "documented"


class ComponentAnalysis(BaseModel):
    component: str
    business_behavior: list[Finding] = Field(default_factory=list)
    responsibilities: list[Finding] = Field(default_factory=list)
    entry_points: list[Finding] = Field(default_factory=list)
    interfaces: list[Finding] = Field(default_factory=list)
    dependencies: list[Finding] = Field(default_factory=list)
    data_flow: list[Finding] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    needs_more_evidence: list[str] = Field(default_factory=list)   # paths/topics not supplied


class RepoAnalysis(BaseModel):
    summary: str = ""
    components: list[ComponentAnalysis] = Field(default_factory=list)
    commands: list[CommandInfo] = Field(default_factory=list)
    project_context: dict[str, str] = Field(default_factory=dict)


class SkillSpec(BaseModel):
    name: str                                             # kebab-case
    kind: Literal["local", "shared_ref", "needs_evidence"] = "local"
    shared_skill_id: str | None = None
    when_to_use: str = ""
    scope: list[str] = Field(default_factory=list)       # paths/globs
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    example: str = ""                                     # existing example path in repo
    sources: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class SkillsResult(BaseModel):
    skills: list[SkillSpec] = Field(default_factory=list)


class HookProposal(BaseModel):
    name: str
    trigger: str                                          # e.g. pre-commit, pre-push, CI on PR
    command: str
    scope: str = ""
    evidence: list[str] = Field(default_factory=list)
    failure_behavior: str = ""
    status: Literal["active", "proposed", "documented"] = "proposed"


class AutomationResult(BaseModel):
    hooks: list[HookProposal] = Field(default_factory=list)
    verification_commands: list[CommandInfo] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PlannedFile(BaseModel):
    path: str                                             # repo-relative
    scope: str = ""
    action: Literal["create", "update", "keep"] = "create"
    purpose: str = ""
    sources: list[str] = Field(default_factory=list)
    content: str | None = None                            # filled by drafting step / renderer


class RagRecommendation(BaseModel):
    recommended: bool = False
    affected_scope: list[str] = Field(default_factory=list)
    reason: str = ""
    missing_for: list[str] = Field(default_factory=list)  # e.g. Requirements, Architect
    material_to_index: list[str] = Field(default_factory=list)


class HarnessPlan(BaseModel):
    files: list[PlannedFile] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    rag: RagRecommendation = Field(default_factory=RagRecommendation)


# --------------------------------------------------------------------------- results

class WriteResult(BaseModel):
    path: str
    action: Literal["created", "updated", "kept", "skipped", "failed", "planned"]
    detail: str = ""
    bytes: int = 0


class Alert(BaseModel):
    kind: Literal["rag", "coverage", "conflict", "write_failure", "llm"]
    severity: Literal["info", "warning", "error"] = "warning"
    affected_scope: list[str] = Field(default_factory=list)
    reason: str
    missing_for: list[str] = Field(default_factory=list)
    suggested_material: list[str] = Field(default_factory=list)


class InitResult(BaseModel):
    """Shape mirrors 01-init/expected.json, with extra operator fields in `output`."""
    fixture: bool = False
    stage: Literal["init"] = "init"
    feature_id: str | None = None
    run_id: str
    status: Literal["completed", "completed_with_warnings", "failed"]
    artifacts: list[str] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)

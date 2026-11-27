"""Structured replies from the LLM (requirement analysis, HLD design) and their parsing."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from agent_architect.platform_paths import InvalidPathError, validate_architecture_path


class DesignParseError(ValueError):
    """The LLM reply is not a valid design."""


class ComponentHLD(BaseModel):
    architecture_path: str = Field(..., description="Folder under components/, e.g. backend/api")
    name: str = Field(..., min_length=1)
    hld: str = Field(..., min_length=1, description="Markdown HLD for this component")

    @field_validator("architecture_path")
    @classmethod
    def _valid_path(cls, value: str) -> str:
        try:
            return validate_architecture_path(value)
        except InvalidPathError as exc:
            raise ValueError(str(exc)) from exc


class ArchitectureDesign(BaseModel):
    overall_hld: str = Field(..., min_length=1, description="Markdown HLD for the whole feature flow")
    components: list[ComponentHLD] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _unique_paths(self) -> "ArchitectureDesign":
        paths = [c.architecture_path.lower() for c in self.components]
        duplicates = sorted({p for p in paths if paths.count(p) > 1})
        if duplicates:
            raise ValueError(f"Duplicate architecture_path values: {', '.join(duplicates)}")
        return self


class RequirementAnalysis(BaseModel):
    summary: str = Field(..., min_length=1)
    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    affected_areas: list[str] = Field(default_factory=list, description="Existing parts of the repo involved")
    assumptions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list, description="Clarifying questions for the user")


ANALYSIS_SCHEMA = """{
  "summary": "<the goal in one or two sentences>",
  "functional_requirements": ["FR-1: ...", "FR-2: ..."],
  "non_functional_requirements": ["NFR-1: ..."],
  "constraints": ["..."],
  "affected_areas": ["<existing component or folder>: <why>"],
  "assumptions": ["..."],
  "questions": ["<clarifying question for the user>"]
}"""

DESIGN_SCHEMA = """{
  "overall_hld": "<Markdown HLD for the entire feature flow>",
  "components": [
    {"architecture_path": "backend/api", "name": "<component name>", "hld": "<Markdown HLD for this component>"}
  ]
}"""

_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _json_object(reply: str) -> dict:
    """Accept a bare JSON object or one inside a ```json fence."""
    match = _FENCE.search(reply)
    candidate = match.group(1) if match else reply[reply.find("{") : reply.rfind("}") + 1]
    if not candidate:
        raise DesignParseError("The reply contains no JSON object.")
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise DesignParseError(f"The reply is not valid JSON: {exc}") from exc


def parse_design(reply: str) -> ArchitectureDesign:
    try:
        return ArchitectureDesign.model_validate(_json_object(reply))
    except ValidationError as exc:
        raise DesignParseError(f"The reply is not a valid design: {exc}") from exc


def parse_analysis(reply: str) -> RequirementAnalysis:
    try:
        return RequirementAnalysis.model_validate(_json_object(reply))
    except ValidationError as exc:
        raise DesignParseError(f"The reply is not a valid requirement analysis: {exc}") from exc

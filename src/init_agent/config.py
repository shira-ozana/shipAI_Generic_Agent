"""INIT settings: non-empty shell variables win, then `./.env` (same rules as generic_agent.config).

Only the INIT keys below are read here. Cursor credentials (CURSOR_API_KEY, CURSOR_MODEL)
are read by `generic_agent.config.load_settings()` and never pass through this module.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr


@dataclass(frozen=True)
class InitSettings:
    # Target repository. Public clone URL from .env (REPO_URL); a request may override it.
    repo_url: str = ""
    repo_base_ref: str = "main"
    work_branch: str = "shipAI"
    workspace_dir: str = "./workspaces"
    # Push: to REPO_URL by default (needs write access), or to PUSH_URL (e.g. a fork).
    push_url: str = ""
    git_token: SecretStr = SecretStr("")
    push_enabled: bool = True
    git_author_name: str = "shipAI init-agent"
    git_author_email: str = "init-agent@users.noreply.github.com"

    # Everything INIT writes lives under this folder of the target repo.
    platform_dir: str = "platform"

    # Context budget: characters of evidence per LLM call after room for prompt + answer.
    context_budget_chars: int = 60000
    max_file_chars: int = 12000
    max_calls_per_component: int = 3
    max_components: int = 25
    # A folder that holds at least this many components gets its own local AGENTS.md.
    local_agents_min_components: int = 3
    docs_rag_threshold_chars: int = 400000

    # Extra prompt-pack folder (rules/, guidelines/, skills/) layered over the built-in one.
    prompts_override_dir: str = ""
    llm_concurrency: int = 2
    json_retries: int = 2

    @property
    def report_path(self) -> str:
        return f"{self.platform_dir}/init/INIT.md"

    @property
    def status_path(self) -> str:
        return f"{self.platform_dir}/init/status.json"


_KEYS = {
    "REPO_URL": ("repo_url", str),
    "REPO_BASE_REF": ("repo_base_ref", str),
    "INIT_BRANCH": ("work_branch", str),
    "INIT_WORKSPACE_DIR": ("workspace_dir", str),
    "PUSH_URL": ("push_url", str),
    "GIT_TOKEN": ("git_token", SecretStr),
    "INIT_PUSH": ("push_enabled", lambda v: v.lower() in ("1", "true", "yes")),
    "GIT_AUTHOR_NAME": ("git_author_name", str),
    "GIT_AUTHOR_EMAIL": ("git_author_email", str),
    "INIT_PLATFORM_DIR": ("platform_dir", str),
    "INIT_CONTEXT_BUDGET_CHARS": ("context_budget_chars", int),
    "INIT_MAX_FILE_CHARS": ("max_file_chars", int),
    "INIT_MAX_CALLS_PER_COMPONENT": ("max_calls_per_component", int),
    "INIT_MAX_COMPONENTS": ("max_components", int),
    "INIT_LOCAL_AGENTS_MIN_COMPONENTS": ("local_agents_min_components", int),
    "INIT_DOCS_RAG_THRESHOLD_CHARS": ("docs_rag_threshold_chars", int),
    "INIT_PROMPTS_DIR": ("prompts_override_dir", str),
    "INIT_LLM_CONCURRENCY": ("llm_concurrency", int),
    "INIT_JSON_RETRIES": ("json_retries", int),
}


def load_init_settings(environ: Mapping[str, str] | None = None, env_file: Path | None = None) -> InitSettings:
    env = os.environ if environ is None else environ
    path = Path.cwd() / ".env" if env_file is None else env_file
    file_values = dotenv_values(path) if path.is_file() else {}
    values = {}
    for key, (attr, cast) in _KEYS.items():
        raw = (env.get(key) or file_values.get(key) or "").strip()
        # `your-...` is the .env.example placeholder: treat it as unset (same rule as
        # generic_agent.config). Without GIT_TOKEN, git uses its own credential helper.
        if raw and not raw.startswith("your-"):
            values[attr] = cast(raw)
    return InitSettings(**values)

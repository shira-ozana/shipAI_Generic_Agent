"""GitHub credentials saved by the SDLC backend (``users`` + ``repositories`` tables).

The agent receives only ``username``, ``repo_url`` and ``feature_id``; the GitHub
password/token never travels in the request. It is read from Supabase and
decrypted with the backend's Fernet ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from agent_architect.config import ArchitectSettings


class CredentialsNotFound(RuntimeError):
    """The user or the repository is not registered in the backend."""


@dataclass(frozen=True)
class GitHubCredentials:
    github_user: str
    token: SecretStr = field(repr=False)


def fetch_github_credentials(
    settings: ArchitectSettings, username: str, repo_url: str, client: Any | None = None
) -> GitHubCredentials:
    if client is None:
        from supabase import create_client

        client = create_client(
            settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
        )

    user = client.table("users").select("user_id").eq("username", username).limit(1).execute()
    if not user.data:
        raise CredentialsNotFound(f"User '{username}' is not registered (POST /api/users).")

    repo = (
        client.table("repositories")
        .select("github_user, github_password_encrypted")
        .eq("user_id", user.data[0]["user_id"])
        .eq("repo_url", repo_url)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    if not repo.data:
        raise CredentialsNotFound(
            f"Repository {repo_url} is not registered for '{username}' (POST /api/repositories)."
        )

    row = repo.data[0]
    try:
        token = Fernet(settings.encryption_key.get_secret_value().encode()).decrypt(
            row["github_password_encrypted"].encode()
        )
    except InvalidToken as exc:
        raise CredentialsNotFound(
            "Could not decrypt the GitHub credentials. ENCRYPTION_KEY must match the backend."
        ) from exc
    return GitHubCredentials(github_user=row["github_user"], token=SecretStr(token.decode()))

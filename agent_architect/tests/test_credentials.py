from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from agent_architect.config import ArchitectSettings
from agent_architect.credentials import CredentialsNotFound, fetch_github_credentials


def _client(user_rows, repo_rows):
    def table(name):
        q = MagicMock()
        for m in ("select", "eq", "order", "limit"):
            getattr(q, m).return_value = q
        q.execute.return_value.data = user_rows if name == "users" else repo_rows
        return q

    client = MagicMock()
    client.table.side_effect = table
    return client


def _settings(key):
    return ArchitectSettings("https://x.supabase.co", SecretStr("srk"), SecretStr(key))


def test_decrypts_backend_token():
    key = Fernet.generate_key().decode()
    enc = Fernet(key.encode()).encrypt(b"ghp_token").decode()
    client = _client([{"user_id": "u1"}], [{"github_user": "octo", "github_password_encrypted": enc}])
    creds = fetch_github_credentials(_settings(key), "shira", "https://github.com/a/b", client)
    assert creds.github_user == "octo" and creds.token.get_secret_value() == "ghp_token"
    assert "ghp_token" not in repr(creds)


def test_unknown_user_or_repo():
    key = Fernet.generate_key().decode()
    with pytest.raises(CredentialsNotFound):
        fetch_github_credentials(_settings(key), "x", "https://github.com/a/b", _client([], []))
    with pytest.raises(CredentialsNotFound):
        fetch_github_credentials(_settings(key), "x", "https://github.com/a/b", _client([{"user_id": "u"}], []))

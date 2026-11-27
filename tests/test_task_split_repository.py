"""Repository access guardrails: scoped, read-only, no credential files."""

from __future__ import annotations

import os
import subprocess

import pytest

from generic_agent.agents.task_split import RepositoryAccess, RepositoryAccessError
from generic_agent.agents.task_split.repository import detect_git_head


def test_lists_reads_and_searches_the_checkout(repository) -> None:
    assert "dir   services/" in repository.list_dir("")
    listing = repository.list_dir("services/order")
    assert "file  status.py" in listing and "tests/" in listing
    text = repository.read_file("services/order/status.py")
    assert "mark_dispatched" in text and "lines 1-6 of 6" in text
    assert "services/notification/consumer.py:5:" in repository.search("welcome email")
    assert repository.search("no-such-text-anywhere") == "No matches."


def test_read_file_pages_by_line_range(repository) -> None:
    text = repository.read_file("services/order/status.py", start_line=4, end_line=5)
    assert "lines 4-5 of 6" in text and "mark_dispatched" in text and "return order" not in text


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../outside.txt", "services/../../outside.txt", "C:/Windows", "\x00"]
)
def test_paths_outside_the_root_are_refused(repository, path) -> None:
    with pytest.raises(RepositoryAccessError):
        repository.read_file(path)
    with pytest.raises(RepositoryAccessError):
        repository.list_dir(path)
    assert repository.contains(path) is False


def test_symlinks_that_leave_the_repository_are_refused(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "ok.txt").write_text("fine")
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP-SECRET")
    os.symlink(outside, root / "link.txt")
    os.symlink(tmp_path, root / "up")
    access = RepositoryAccess("r", root, "rev1")

    assert access.read_file("ok.txt").endswith("fine")
    for path in ("link.txt", "up/secret.txt"):
        with pytest.raises(RepositoryAccessError):
            access.read_file(path)
    assert "link.txt" not in access.list_dir("") and "up/" not in access.list_dir("")
    assert "TOP-SECRET" not in access.search("secret")


def test_credential_files_and_git_internals_are_never_readable(tmp_path) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("url = https://user:token@example.com/x.git")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("TOKEN-IN-CODE = 1  # ok to read")
    for name in (".env", ".env.production", "deploy.pem", "id_rsa", ".npmrc", "credentials.json"):
        (root / name).write_text("password=hunter2")
    access = RepositoryAccess("r", root, "rev1")

    for name in (".env", ".env.production", "deploy.pem", "id_rsa", ".npmrc", ".git/config", "credentials.json"):
        with pytest.raises(RepositoryAccessError):
            access.read_file(name)
        assert access.contains(name) is False
    listing = access.list_dir("")
    assert "src/" in listing and all(n not in listing for n in (".env", ".git", "id_rsa", ".pem"))
    found = access.search("hunter2") + access.search("token")
    assert "hunter2" not in found and "user:token" not in found
    assert "src/app.py:1:" in access.search("token-in-code")


def test_allowed_paths_narrow_the_readable_area(repository) -> None:
    scoped = RepositoryAccess("shop", repository._root, "fixture-rev-1", allowed_paths=["services/order"])

    assert scoped.contains("services/order/status.py")
    assert not scoped.contains("services/notification/consumer.py")
    assert not scoped.contains("contracts/order_created.json")
    with pytest.raises(RepositoryAccessError):
        scoped.read_file("services/notification/consumer.py")
    assert scoped.search("welcome email") == "No matches."
    assert "dir   services/" in scoped.list_dir("")
    assert "contracts/" not in scoped.list_dir("") and "notification" not in scoped.list_dir("services")


def test_binary_and_oversized_files_are_refused(tmp_path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / "big.txt").write_text("x" * (2 * 1024 * 1024))
    access = RepositoryAccess("r", tmp_path, "rev1")
    for name in ("blob.bin", "big.txt"):
        with pytest.raises(RepositoryAccessError):
            access.read_file(name)


def test_error_messages_do_not_reveal_absolute_paths(repository) -> None:
    for call in (
        lambda: repository.read_file("/etc/passwd"),
        lambda: repository.read_file("services/order/missing.py"),
        lambda: repository.list_dir("../x"),
    ):
        with pytest.raises(RepositoryAccessError) as info:
            call()
        assert str(repository._root) not in str(info.value)


def test_cursor_tools_expose_only_the_three_read_only_operations(repository) -> None:
    tools = repository.tools()
    assert set(tools) == {"repo_list_dir", "repo_read_file", "repo_search"}
    assert "mark_dispatched" in tools["repo_read_file"].execute({"path": "services/order/status.py"}, None)
    assert "consumer.py" in tools["repo_list_dir"].execute({"path": "services/notification"}, None)
    assert "welcome" in tools["repo_search"].execute({"query": "welcome"}, None)
    with pytest.raises(RepositoryAccessError):
        tools["repo_read_file"].execute({"path": "../../x"}, None)
    with pytest.raises(RepositoryAccessError):
        tools["repo_read_file"].execute({"path": "a.py", "start_line": "1"}, None)
    with pytest.raises(RepositoryAccessError):
        tools["repo_read_file"].execute({}, None)


@pytest.mark.parametrize("repository_id", ["", "has space", "../x", "a" * 80])
def test_repository_id_must_be_a_short_identifier(tmp_path, repository_id) -> None:
    with pytest.raises(ValueError, match="repository_id"):
        RepositoryAccess(repository_id, tmp_path, "rev1")


def test_revision_is_required_and_must_be_short_text(tmp_path) -> None:
    with pytest.raises(ValueError, match="revision"):
        RepositoryAccess("r", tmp_path, 'x" onload="evil')
    with pytest.raises(ValueError, match="revision is required"):
        RepositoryAccess.from_checkout("r", tmp_path)


def _git(root, *args) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()


def test_git_checkout_revision_is_detected_and_verified(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("a")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "init")
    head = _git(root, "rev-parse", "HEAD")

    assert detect_git_head(root) == head
    assert RepositoryAccess.from_checkout("r", root).revision == head
    assert RepositoryAccess.from_checkout("r", root, head[:12]).revision == head[:12]
    with pytest.raises(RepositoryAccessError, match="selected revision"):
        RepositoryAccess.from_checkout("r", root, "deadbeef")
    assert detect_git_head(root / "sub") is None

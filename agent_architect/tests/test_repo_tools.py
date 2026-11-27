import pytest

from agent_architect.repo_tools import list_files, read_file


def test_read_and_list_inside_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print(1)")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret")
    listing = list_files(tmp_path)
    assert "src/a.py" in listing and ".git" not in listing
    assert read_file(tmp_path, "src/a.py") == "print(1)"


@pytest.mark.parametrize("path", ["../outside.txt", ".git/config", "/etc/passwd"])
def test_cannot_escape_repo(tmp_path, path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret")
    (tmp_path.parent / "outside.txt").write_text("nope")
    with pytest.raises(ValueError):
        read_file(tmp_path, path)

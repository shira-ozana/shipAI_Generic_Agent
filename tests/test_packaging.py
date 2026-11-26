from __future__ import annotations

import importlib
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_langgraph_json_points_to_the_studio_factory() -> None:
    config = json.loads((ROOT / "langgraph.json").read_text())
    file_part, _, attr = config["graphs"]["agent"].partition(":")
    assert (ROOT / file_part).is_file()
    assert Path(file_part).as_posix() == "src/generic_agent/studio.py"
    module = importlib.import_module("generic_agent.studio")
    assert callable(getattr(module, attr))
    assert "." in config["dependencies"]


def test_studio_factory_builds_the_same_graph_without_checkpointer(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here; values come from the environment
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-secret-value")
    monkeypatch.setenv("CURSOR_MODEL", "some-model")
    from generic_agent.studio import make_graph

    graph = make_graph()
    assert graph.checkpointer is None
    assert set(graph.get_graph().nodes) == {"__start__", "run_cursor_agent", "__end__"}


def test_prompt_file_is_inside_the_package() -> None:
    import generic_agent

    assert (Path(generic_agent.__file__).parent / "prompts" / "default_system.md").is_file()


def test_pyproject_depends_on_cursor_sdk_not_anthropic() -> None:
    text = (ROOT / "pyproject.toml").read_text()
    assert '"cursor-sdk' in text
    assert "anthropic" not in text


def test_agents_package_dir_is_reserved_and_empty() -> None:
    import generic_agent

    agents = Path(generic_agent.__file__).parent / "agents"
    assert [p.name for p in agents.iterdir()] == [".gitkeep"]

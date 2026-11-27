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


def test_task_split_is_its_own_package_with_packaged_prompt_assets() -> None:
    import task_split_agent
    from task_split_agent.prompt_pack import SKILL_IDS, PromptPack

    package = Path(task_split_agent.__file__).parent
    for name in ("planner.py", "schema.py", "repository.py", "prompt_pack.py"):
        assert (package / name).is_file()
    assets = package / "prompts"
    assert (assets / "core.md").is_file() and (assets / "guardrails.md").is_file()
    assert sorted((assets / "rules").glob("*.md")) and sorted((assets / "guidelines").glob("*.md"))
    assert SKILL_IDS == (
        "hls-analysis", "repository-inspection", "task-decomposition", "acceptance-criteria", "dependency-mapping",
    )
    assert all((assets / "skills" / name / "SKILL.md").is_file() for name in SKILL_IDS)
    # The old locations are gone: no prompt assets left under generic_agent.
    import generic_agent

    assert not (Path(generic_agent.__file__).parent / "prompts" / "task_split").exists()

    # The files are read through importlib.resources, as they are from an installed wheel.
    from importlib import resources

    root = resources.files("task_split_agent").joinpath("prompts")
    assert root.joinpath("skills", "hls-analysis", "SKILL.md").is_file()
    pack = PromptPack.load()
    assert len(pack.run_input("plan")["skills"]) == 5


def test_wheel_and_sdist_include_the_task_split_package() -> None:
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    build = config["tool"]["hatch"]["build"]["targets"]
    assert "src/task_split_agent" in build["wheel"]["packages"]
    assert "src/task_split_agent" in build["sdist"]["include"]
    # The other packages stay in place.
    assert {"src/generic_agent", "src/init_agent"} <= set(build["wheel"]["packages"])


def test_every_markdown_asset_in_the_package_has_frontmatter_with_a_unique_id() -> None:
    from importlib import resources

    from generic_agent.prompt_docs import parse_prompt_doc, walk_markdown

    files = list(walk_markdown(resources.files("task_split_agent").joinpath("prompts")))
    assert all(text.startswith("---") for _, text in files)
    docs = [parse_prompt_doc(text, rel) for rel, text in files]
    assert len({d.id for d in docs}) == len(docs) == 1 + 1 + 8 + 4 + 5
    assert {d.kind for d in docs} == {"core", "guardrail", "rule", "guideline", "skill"}

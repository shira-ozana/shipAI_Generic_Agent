"""INIT agent tests: offline, Cursor mocked through the shared `mock_cursor` fixture."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from init_agent.config import InitSettings, load_init_settings
from init_agent.llm import GenericAgent, extract_json
from init_agent.models import PlannedFile
from init_agent.prompt_pack import PromptPack
from init_agent.repo.scanner import component_of, scan_repository
from init_agent.repo.writer import (
    BEGIN, check_links, generated_block, is_harness_path, normalize, replace_generated_block, unlink, write_file,
)
from init_agent.graph import hub_folders, skill_folder
from init_agent.service import arun_init

# --------------------------------------------------------------------------- fixtures

FILES = {
    "README.md": "# Conduit\n\n```bash\nbun run setup\nbun run start\nbun run build\nbun run test\n```\n",
    "CLAUDE.md": "Use standalone components. Run `bun run test` before a PR.\n",
    "package.json": json.dumps({"name": "conduit", "scripts": {"start": "ng serve", "build": "ng build",
                                                               "test": "vitest run", "lint": "eslint ."}}),
    "bun.lock": "lock",
    "angular.json": "{}",
    ".husky/pre-commit": "bunx lint-staged\n",
    ".github/workflows/ci.yml": "jobs:\n  test:\n    steps:\n      - run: bun run test\n",
    "src/main.ts": "bootstrapApplication(AppComponent, appConfig);\n",
    "src/app/app.routes.ts": "export const routes = [{ path: 'article/:slug', loadComponent: () => ArticleComponent }];\n",
    "src/app/features/article/pages/article/article.component.ts":
        "export class ArticleComponent { article$ = this.articles.get(this.slug); }\n",
    "src/app/features/article/services/articles.service.ts":
        "export class ArticlesService { get(slug) { return this.http.get(`/articles/${slug}`); } }\n",
    "src/app/features/article/pages/article/article.component.spec.ts": "describe('ArticleComponent', () => {});\n",
    "src/app/core/auth/user.service.ts": "export class UserService {}\n",
    # src/app/features holds 3 components (article, profile, settings) -> it gets a local AGENTS.md
    "src/app/features/profile/profile.component.ts": "export class ProfileComponent {}\n",
    "src/app/features/settings/settings.component.ts": "export class SettingsComponent {}\n",
}

OVERVIEW = {
    "summary": "Conduit is an Angular frontend over the RealWorld API.",
    "commands": [{"command": "bun run build", "source": "README.md", "status": "executed_passed"}],
    "project_context": {"application": "Conduit", "article_detail_route": "/article/:slug",
                        "start_hint": "bun run start (documented)"},
}
COMPONENT = {
    "component": "X",
    "business_behavior": [{"statement": "Shows one article by slug.",
                           "evidence": ["src/app/features/article/pages/article/article.component.ts"]}],
    "interfaces": [{"statement": "GET /articles/:slug",
                    "evidence": ["src/app/features/article/services/articles.service.ts"]}],
    "coverage_gaps": ["comments flow not shown"],
}
SKILLS = {"skills": [
    {"name": "Add Feature Page", "kind": "local", "when_to_use": "Add a page under a feature.",
     "scope": ["src/app/features/**"], "steps": ["Create the component.", "Add the route."],
     "example": "src/app/features/article/pages/article/article.component.ts",
     "sources": ["src/app/app.routes.ts"], "verification": ["bun run test (documented)"]},
    {"name": "write-e2e-test", "kind": "needs_evidence", "missing_evidence": ["an existing e2e test"]},
    # Marked shared, but the request has no shared catalog -> INIT makes it a local skill.
    {"name": "run-unit-tests", "kind": "shared_ref", "shared_skill_id": "run-unit-tests",
     "when_to_use": "Run the unit tests.", "scope": ["src/**"], "steps": ["Run `bun run test`."]},
]}
AUTOMATION = {"hooks": [
    {"name": "husky pre-commit", "trigger": "pre-commit", "command": "bunx lint-staged", "status": "active",
     "evidence": [".husky/pre-commit"], "failure_behavior": "blocks the commit"},
    {"name": "agent test", "trigger": "agent: after edit", "command": "bun run test", "status": "active",
     "evidence": ["package.json"], "failure_behavior": "report"},
], "verification_commands": [{"command": "bun run test", "source": "package.json", "status": "executed_passed"}]}
PLAN = {"files": [
    {"path": "platform/AGENTS.md", "scope": "repository", "action": "create", "purpose": "Entry point"},
    {"path": "ARCHITECTURE.md", "scope": "repository", "action": "create", "purpose": "System map",
     "sources": ["src/main.ts", "src/does/not/exist.ts"]},
    {"path": "areas/src/app/features/article/AGENTS.md", "scope": "src/app/features/article", "action": "create",
     "purpose": "Article feature contract"},
    {"path": "../../evil.md", "action": "create", "purpose": "escape"},
    {"path": "src/app/main.ts", "action": "create", "purpose": "app code"},
    {"path": "src/app/core/auth/AGENTS.md", "scope": "src/app/core/auth", "action": "create", "purpose": "Auth"},
    {"path": "src/nope/AGENTS.md", "action": "create", "purpose": "folder does not exist"},
], "conflicts": [], "recommendations": ["Add an e2e test for article detail."], "coverage_gaps": []}

DRAFTS = {
    "AGENTS.md": "# Agents\n\nSee [ARCHITECTURE](ARCHITECTURE.md) and "
                 "[skill](src/app/features/skills/add-feature-page/SKILL.md).\n",
    # Broken on purpose: DEVELOPMENT.md is not planned -> the repair call fixes it.
    "ARCHITECTURE.md": "# Architecture\n\n## System\n\nConduit shows articles. See [main](src/main.ts). "
                       "Commands: [dev](DEVELOPMENT.md).\n",
    # Broken on purpose: the repair call does not fix it -> INIT turns it into plain text.
    "RULES.md": "# Rules\n\n- RULE-01: Run `bun run test`. Basis: [CLAUDE.md](CLAUDE.md). "
                "Skill: [ghost](skills/ghost/SKILL.md).\n",
    "src/app/features/article/AGENTS.md":
        "# Article\n\nUse [add-feature-page](../skills/add-feature-page/SKILL.md). See [rules](../../../../RULES.md)."
        " Report: [INIT](../../../../platform/init/INIT.md).\n",
}


def _fence(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


def responder(message: str, _options) -> str:
    if "Node: analyze_overview" in message:
        return _fence(OVERVIEW)
    if "Node: analyze_repository" in message:
        return "Here you go:\n" + _fence(COMPONENT)
    if "Node: derive_skills" in message:
        return _fence(SKILLS)
    if "Node: assess_automation" in message:
        return _fence(AUTOMATION)
    if "Node: plan_harness" in message:
        return _fence(PLAN)
    if "Node: repair_links" in message:
        if "File: `ARCHITECTURE.md`" in message:
            return DRAFTS["ARCHITECTURE.md"].replace("[dev](DEVELOPMENT.md)", "[rules](RULES.md)")
        return DRAFTS["RULES.md"]                                          # no fix offered
    if "Write the Markdown body of" in message:
        for path, body in DRAFTS.items():
            if f"Write the Markdown body of `{path}`." in message:
                return "```markdown\n" + body + "```"
        return "# Generated\n"
    raise AssertionError("unexpected prompt")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def init_settings(tmp_path: Path) -> InitSettings:
    return InitSettings(workspace_dir=str(tmp_path / "ws"), llm_concurrency=1)


def _app_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith("platform/") and not is_harness_path(rel) and ".git" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + p.read_bytes())
    return h.hexdigest()


async def _run(repo: Path, settings, init_settings, **inputs):
    req = {"feature_id": "RW-READING-TIME", "project": {"repo_path": str(repo)}, "inputs": inputs}
    return await arun_init(req, agent=GenericAgent(settings=settings), settings=init_settings)


# --------------------------------------------------------------------------- end to end

async def test_full_run_writes_harness_into_repo_folders(repo, settings, init_settings, mock_cursor):
    fake = mock_cursor(responder)
    before = _app_hash(repo)
    result = await _run(repo, settings, init_settings)

    assert result.stage == "init" and result.feature_id == "RW-READING-TIME"
    assert result.status == "completed_with_warnings"                    # three rejected paths
    p = repo / "platform"
    for rel in ("AGENTS.md", "ARCHITECTURE.md", "RULES.md", "HOOKS.md",
                "src/app/features/article/AGENTS.md", "src/app/core/auth/AGENTS.md",
                # folders with 3+ components get a local AGENTS.md even though the plan left them out
                "src/AGENTS.md", "src/app/AGENTS.md", "src/app/features/AGENTS.md",
                # the skill's scope is src/app/features/** -> it lives next to that code
                "src/app/features/skills/add-feature-page/SKILL.md",
                "platform/init/INIT.md", "platform/init/status.json"):
        assert (repo / rel).is_file(), rel
    assert not (repo / "skills").exists()                                 # no root skill in this fixture
    assert (repo / "src/skills/run-unit-tests/SKILL.md").is_file()        # invalid shared_ref -> local
    derive_prompt = next(m for m in fake.prompts if "Node: derive_skills" in m)
    assert "Shared skill catalog ids (the only valid `shared_skill_id` values)\nnone" in derive_prompt
    assert not (repo / "src/app/core/AGENTS.md").exists()                 # 1 component: not a hub
    assert sorted(x.name for x in p.iterdir()) == ["init"]                # platform/ holds the report only
    assert not (repo / "areas").exists() and not (repo / "src/nope").exists()
    assert _app_hash(repo) == before                                      # application code untouched
    assert not (repo.parent / "evil.md").exists()

    actions = {f["path"]: f["action"] for f in result.output["files"]}
    assert actions["../../evil.md"] == "failed"
    assert actions["src/app/main.ts"] == "failed"
    assert actions["src/nope/AGENTS.md"] == "failed"
    assert actions["AGENTS.md"] == "created"                              # legacy platform/ path normalized
    assert actions["src/app/features/article/AGENTS.md"] == "created"     # legacy areas/ path normalized
    assert result.output["project_context"]["article_detail_route"] == "/article/:slug"
    assert "platform/init/INIT.md" in result.artifacts

    report = (p / "init/INIT.md").read_text(encoding="utf-8")
    assert "Markdown files created or updated: **11**" in report
    assert "add-feature-page" in report and "needs evidence" in report
    assert "(local, `src/app/features`)" in report

    hub_prompt = next(m for m in fake.prompts if "Write the Markdown body of `src/app/features/AGENTS.md`." in m)
    assert "Components inside this folder: ['src/app/features/article'" in hub_prompt
    assert "Skills that live in this folder" in hub_prompt and "src/app/features/skills/add-feature-page" in hub_prompt
    plan_prompt = next(m for m in fake.prompts if "Node: plan_harness" in m)
    assert "`src/app/features/AGENTS.md`: 3 components" in plan_prompt
    assert "not** performed" in report                                    # STE100 review status
    assert "src/does/not/exist.ts" in report                              # unknown evidence reported
    assert "Broken link" not in report and "broken link" not in json.dumps(result.output["alerts"])

    # Links: the draft prompt carries exact targets; broken ones are repaired or unlinked.
    article_prompt = next(m for m in fake.prompts
                          if "Write the Markdown body of `src/app/features/article/AGENTS.md`." in m)
    assert "`RULES.md` (" in article_prompt and "link target `../../../../RULES.md`" in article_prompt
    assert "Skill: link harness files" in article_prompt.split("</instructions>")[0]
    repair_prompts = [m for m in fake.prompts if "Node: repair_links" in m]
    assert len(repair_prompts) == 2 and "Skill: repair broken links" in repair_prompts[0]
    arch = (repo / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "[rules](RULES.md)" in arch and "DEVELOPMENT.md" not in arch
    rules = (repo / "RULES.md").read_text(encoding="utf-8")
    assert "Skill: ghost." in rules and "skills/ghost" not in rules
    assert any("skills/ghost/SKILL.md" in a["reason"] for a in result.output["alerts"])
    written = [f["path"] for f in result.output["files"] if f["action"] == "created"]
    assert check_links(repo, written) == []

    # R7 / R3 enforced in code, not just in the prompt
    hooks = (repo / "HOOKS.md").read_text(encoding="utf-8")
    assert "| husky pre-commit | active |" in hooks
    assert "| agent test | proposed |" in hooks
    assert "executed_passed" not in hooks

    # The prompt pack is the Generic Agent's system prompt (rendered into <instructions>)
    first = fake.prompts[0]
    instructions = first.split("</instructions>")[0]
    assert "You prepare an existing software repository" in instructions
    assert "R1: Evidence is required" in instructions and "Skill: repository overview" in instructions
    arch_prompt = next(m for m in fake.prompts if "Write the Markdown body of `ARCHITECTURE.md`." in m)
    assert "ASD-STE100" in arch_prompt.split("</instructions>")[0]
    assert all(opts.local.custom_tools is None and opts.tools == [] for _, opts in fake.calls)


async def test_rerun_preserves_human_content(repo, settings, init_settings, mock_cursor):
    mock_cursor(responder)
    (repo / "AGENTS.md").write_text("# Team notes\n\nKeep this line.\n", encoding="utf-8")
    await _run(repo, settings, init_settings)
    text = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# Team notes") and "Keep this line." in text and BEGIN in text

    await _run(repo, settings, init_settings)                              # second run: block replaced, not duplicated
    text = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and "Keep this line." in text
    status = json.loads((repo / "platform/init/status.json").read_text(encoding="utf-8"))
    assert status["revision"] == 2


async def test_rag_alert_returned_to_operator(repo, settings, init_settings, mock_cursor):
    mock_cursor(responder)
    result = await _run(repo, settings, init_settings, retrieval_gaps=["comments API docs"])
    rag = result.output["rag_alert"]
    assert rag and "comments API docs" in rag["suggested_material"]
    assert "## RAG alert" in (repo / "platform/init/INIT.md").read_text(encoding="utf-8")


async def test_dry_run_writes_nothing(repo, settings, init_settings, mock_cursor):
    mock_cursor(responder)
    req = {"project": {"repo_path": str(repo)}, "dry_run": True}
    result = await arun_init(req, agent=GenericAgent(settings=settings), settings=init_settings)
    assert not (repo / "platform").exists() and not (repo / "AGENTS.md").exists()
    assert not (repo / "src/app/features/article/AGENTS.md").exists()
    assert any(f["action"] == "planned" for f in result.output["files"])


async def test_llm_failure_does_not_crash(repo, settings, init_settings, mock_cursor):
    mock_cursor(lambda m, o: "I cannot do that.")
    result = await _run(repo, settings, init_settings)
    assert result.status == "completed_with_warnings"
    assert (repo / "platform/init/INIT.md").is_file()
    assert any(a["kind"] == "llm" for a in result.output["alerts"])


# --------------------------------------------------------------------------- clone, commit, push

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


async def test_clone_branch_commit_push(repo, tmp_path, settings, mock_cursor):
    mock_cursor(responder)
    _git(repo, "init", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(repo), str(remote))

    s = InitSettings(repo_url=str(remote), workspace_dir=str(tmp_path / "ws"), llm_concurrency=1)
    result = await arun_init({"feature_id": "F"}, agent=GenericAgent(settings=settings), settings=s)
    assert result.output["pushed"] is True and result.output["branch"] == "shipAI"
    files = _git(remote, "ls-tree", "-r", "--name-only", "shipAI").splitlines()
    assert "platform/init/INIT.md" in files and "AGENTS.md" in files
    assert "src/app/features/article/AGENTS.md" in files
    changed = _git(remote, "diff", "--name-only", "main", "shipAI").splitlines()
    assert changed and all(f.startswith("platform/init/") or is_harness_path(f) for f in changed)


# --------------------------------------------------------------------------- units

async def test_moved_init_skill_is_removed_human_skill_kept(repo, settings, init_settings, mock_cursor):
    mock_cursor(responder)
    old = repo / "skills/add-feature-page/SKILL.md"                       # where an earlier run put it
    old.parent.mkdir(parents=True)
    old.write_text(f"{BEGIN}\n# Skill: old\n<!-- init-agent:end -->\n", encoding="utf-8")
    human = repo / "skills/team-skill/SKILL.md"
    human.parent.mkdir(parents=True)
    human.write_text(f"# Team skill\n\n{BEGIN}\nold\n<!-- init-agent:end -->\n", encoding="utf-8")
    stale = repo / "skills/not-derived-this-time/SKILL.md"                # INIT-only, but not moved
    stale.parent.mkdir(parents=True)
    stale.write_text(f"{BEGIN}\n# Skill: stale\n<!-- init-agent:end -->\n", encoding="utf-8")
    # An earlier INIT file that this run does not plan still links to the old skill path.
    (repo / "GUIDELINES.md").write_text(
        f"# Team guide\n\n{BEGIN}\nUse [the skill](skills/add-feature-page/SKILL.md#steps).\n<!-- init-agent:end -->\n",
        encoding="utf-8")
    fake = mock_cursor(responder)
    result = await _run(repo, settings, init_settings)
    actions = {f["path"]: f["action"] for f in result.output["files"]}
    assert actions["skills/add-feature-page/SKILL.md"] == "removed" and not old.exists()
    assert (repo / "src/app/features/skills/add-feature-page/SKILL.md").is_file()
    assert human.is_file() and "skills/team-skill/SKILL.md" not in actions
    assert stale.is_file() and "skills/not-derived-this-time/SKILL.md" not in actions  # one answer never deletes
    derive_prompt = next(m for m in fake.prompts if "Node: derive_skills" in m)
    assert "'add-feature-page'" in derive_prompt and "'not-derived-this-time'" in derive_prompt

    # The link to the moved skill is fixed in code (no model call) and the file is committed.
    guide = (repo / "GUIDELINES.md").read_text(encoding="utf-8")
    assert "](src/app/features/skills/add-feature-page/SKILL.md)" in guide and guide.startswith("# Team guide")
    assert actions["GUIDELINES.md"] == "updated"
    assert not any("File: `GUIDELINES.md`" in m for m in fake.prompts if "Node: repair_links" in m)
    report = (repo / "platform/init/INIT.md").read_text(encoding="utf-8")
    assert "](../../skills/add-feature-page/SKILL.md)" not in report      # removed file is not linked
    written = [f["path"] for f in result.output["files"] if f["action"] in ("created", "updated", "kept")]
    assert check_links(repo, written + ["platform/init/INIT.md"]) == []


def test_hub_folders_and_skill_folder(tmp_path):
    for d in ("src/app/features/a", "src/app/features/b", "src/app/features/c", "src/app/core/x", "lib"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "src/app/features/a/page.ts").write_text("", encoding="utf-8")
    comps = ["(root)", "src/app/features/a", "src/app/features/b", "src/app/features/c", "src/app/core/x", "lib"]
    hubs = hub_folders(comps, tmp_path, 3)
    assert list(hubs) == ["src", "src/app", "src/app/features"]
    assert hubs["src/app/features"] == ["src/app/features/a", "src/app/features/b", "src/app/features/c"]
    assert hub_folders(comps, tmp_path, 5) == {}
    homes = {"src/app", "src/app/features", "src/app/features/a"}
    assert skill_folder(["src/app/features/**"], homes, tmp_path) == "src/app/features"
    assert skill_folder(["src/app/features/a/page.ts"], homes, tmp_path) == "src/app/features/a"
    assert skill_folder(["src/app/features/a/**", "src/app/core/**"], homes, tmp_path) == "src/app"
    assert skill_folder(["src/**", "lib/**"], homes, tmp_path) is None
    assert skill_folder([], homes, tmp_path) is None


def test_component_of():
    assert component_of("src/app/features/article/pages/a.ts") == "src/app/features/article"
    assert component_of("src/main.ts") == "src"
    assert component_of("README.md") == "(root)"


def test_scanner_categories(repo):
    inv = scan_repository(repo)
    assert "package.json" in inv.manifests and "bun.lock" in inv.lockfiles
    assert ".husky/pre-commit" in inv.hooks and ".github/workflows/ci.yml" in inv.ci
    assert "CLAUDE.md" in inv.guidance
    assert any(c["command"] == "bun run setup" for c in inv.readme_commands)
    assert "bun.lock" not in inv.paths()                                    # lockfiles never reach the model


def test_writer_rejects_paths_outside_catalog(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules/lib").mkdir(parents=True)
    (tmp_path / "platform").mkdir()
    for bad in ("../x.md", "/etc/x.md", "C:/x.md", "src/app.ts", "skills/Bad Name/SKILL.md", "notes.md",
                "missing/AGENTS.md", "node_modules/lib/AGENTS.md", "platform/AGENTS.md", "src/README.md"):
        r = write_file(tmp_path, "platform", PlannedFile(path=bad, content="x"), dry_run=False)
        assert r.action == "failed", bad
    for good in ("skills/run-tests/SKILL.md", "AGENTS.md", "src/AGENTS.md"):
        ok = write_file(tmp_path, "platform", PlannedFile(path=good, content="# x"), False)
        assert ok.action == "created" and (tmp_path / good).is_file(), good
    assert normalize("platform/areas/src/AGENTS.md", "platform") == "src/AGENTS.md"


def test_check_links(tmp_path):
    (tmp_path / "platform").mkdir()
    (tmp_path / "platform/A.md").write_text("[ok](B.md) [bad](C.md) [web](https://x.y)", encoding="utf-8")
    (tmp_path / "platform/B.md").write_text("", encoding="utf-8")
    assert check_links(tmp_path, ["platform/A.md"]) == ["platform/A.md -> C.md"]
    assert check_links(tmp_path, ["platform/A.md"], pending=["platform/C.md"]) == []


def test_generated_block_and_unlink():
    text = f"# Human\n\nKeep.\n\n{BEGIN}\nbody [a](x.md) [b](y.md)\n<!-- init-agent:end -->\n"
    assert generated_block(text) == "body [a](x.md) [b](y.md)"
    assert unlink("body [a](x.md) [b](y.md)", ["x.md"]) == "body a [b](y.md)"
    new = replace_generated_block(text, "fixed")
    assert new.startswith("# Human\n\nKeep.") and generated_block(new) == "fixed" and new.count(BEGIN) == 1


def test_prompt_pack_override_and_operator_rules(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules/r1.md").write_text('---\nid: R1\ntitle: Team evidence rule\nkind: rule\napplies_to: ["*"]\n---\nCite line numbers.\n',
                                          encoding="utf-8")
    pack = PromptPack.load(str(tmp_path))
    text = pack.compose("analyze_repository", extra_rules=["Write in British English."])
    assert "Team evidence rule" in text and "Cite line numbers." in text
    assert "Evidence is required" not in text
    assert "OP-R1: operator rule\nWrite in British English." in text
    assert "write-agents-md" not in pack.ids_for("analyze_repository")


def test_extract_json_variants():
    assert extract_json('noise {"a": {"b": "}"}} tail') == {"a": {"b": "}"}}
    assert extract_json("```json\n{\"x\": 1}\n```") == {"x": 1}


def test_settings_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("REPO_URL=https://github.com/o/r\nINIT_BRANCH=shipAI\nGIT_TOKEN=secret-token\n", encoding="utf-8")
    s = load_init_settings(environ={}, env_file=env)
    assert s.repo_url == "https://github.com/o/r" and s.work_branch == "shipAI"
    assert "secret-token" not in repr(s)
    env.write_text("GIT_TOKEN=your-github-token\n", encoding="utf-8")
    assert load_init_settings(environ={}, env_file=env).git_token.get_secret_value() == ""


def test_api_start_and_poll(repo, mock_cursor, monkeypatch, tmp_path):
    mock_cursor(responder)
    monkeypatch.setenv("CURSOR_API_KEY", "k")
    monkeypatch.setenv("CURSOR_MODEL", "m")
    monkeypatch.setenv("INIT_WORKSPACE_DIR", str(tmp_path / "ws"))
    from init_agent.api import create_app
    client = TestClient(create_app())
    res = client.post("/api/init?wait=true", json={"feature_id": "F", "project": {"repo_path": str(repo)}})
    assert res.status_code == 202 and res.json()["status"] in ("completed", "completed_with_warnings")
    run_id = res.json()["run_id"]
    run = client.get(f"/api/init/{run_id}").json()
    assert [p["node"] for p in run["progress"]][:2] == ["prepare_workspace", "scan_repository"]
    report = client.get(f"/api/init/{run_id}/report")
    assert report.status_code == 200 and report.text.startswith("# INIT report")
    assert client.get("/api/init/nope").status_code == 404

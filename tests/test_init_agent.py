"""INIT agent tests: offline, Cursor mocked through the shared `mock_cursor` fixture."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from init_agent.config import InitSettings, load_init_settings
from init_agent.llm import GenericAgentModel, extract_json
from init_agent.models import PlannedFile
from init_agent.prompt_pack import PromptPack
from init_agent.repo.scanner import component_of, scan_repository
from init_agent.repo.writer import BEGIN, check_links, write_file
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
], "conflicts": [], "recommendations": ["Add an e2e test for article detail."], "coverage_gaps": []}

DRAFTS = {
    "AGENTS.md": "# Agents\n\nSee [ARCHITECTURE](ARCHITECTURE.md) and [skill](skills/add-feature-page/SKILL.md).\n",
    "ARCHITECTURE.md": "# Architecture\n\n## System\n\nConduit shows articles. See [main](../src/main.ts).\n",
    "RULES.md": "# Rules\n\n- RULE-01: Run `bun run test`. Basis: [CLAUDE.md](../CLAUDE.md).\n",
    "areas/src/app/features/article/AGENTS.md":
        "# Article\n\nUse [add-feature-page](../../../../../skills/add-feature-page/SKILL.md). See [rules](../../../../../RULES.md).\n",
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
    if "Write the Markdown body of" in message:
        for path, body in DRAFTS.items():
            if f"Write the Markdown body of `platform/{path}`." in message:
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
        if p.is_file() and "platform" not in p.relative_to(root).parts and ".git" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + p.read_bytes())
    return h.hexdigest()


async def _run(repo: Path, settings, init_settings, **inputs):
    req = {"feature_id": "RW-READING-TIME", "project": {"repo_path": str(repo)}, "inputs": inputs}
    return await arun_init(req, llm=GenericAgentModel(settings=settings), settings=init_settings)


# --------------------------------------------------------------------------- end to end

async def test_full_run_writes_harness_under_platform(repo, settings, init_settings, mock_cursor):
    fake = mock_cursor(responder)
    before = _app_hash(repo)
    result = await _run(repo, settings, init_settings)

    assert result.stage == "init" and result.feature_id == "RW-READING-TIME"
    assert result.status == "completed_with_warnings"                    # two rejected paths
    p = repo / "platform"
    for rel in ("AGENTS.md", "ARCHITECTURE.md", "RULES.md", "HOOKS.md", "skills/add-feature-page/SKILL.md",
                "areas/src/app/features/article/AGENTS.md", "init/INIT.md", "init/status.json"):
        assert (p / rel).is_file(), rel
    assert _app_hash(repo) == before                                      # application code untouched
    assert not (repo.parent / "evil.md").exists()

    actions = {f["path"]: f["action"] for f in result.output["files"]}
    assert actions["platform/../../evil.md"] == "failed"
    assert actions["platform/src/app/main.ts"] == "failed"
    assert result.output["project_context"]["article_detail_route"] == "/article/:slug"
    assert "platform/init/INIT.md" in result.artifacts

    report = (p / "init/INIT.md").read_text(encoding="utf-8")
    assert "Markdown files created or updated: **6**" in report
    assert "add-feature-page" in report and "needs evidence" in report
    assert "not** performed" in report                                    # STE100 review status
    assert "src/does/not/exist.ts" in report                              # unknown evidence reported
    assert "Broken link" not in report

    # R7 / R3 enforced in code, not just in the prompt
    hooks = (p / "HOOKS.md").read_text(encoding="utf-8")
    assert "| husky pre-commit | active |" in hooks
    assert "| agent test | proposed |" in hooks
    assert "executed_passed" not in hooks

    # The prompt pack is the Generic Agent's system prompt (rendered into <instructions>)
    first = fake.prompts[0]
    instructions = first.split("</instructions>")[0]
    assert "You prepare an existing software repository" in instructions
    assert "R1: Evidence is required" in instructions and "Skill: repository overview" in instructions
    arch_prompt = next(m for m in fake.prompts if "`platform/ARCHITECTURE.md`" in m)
    assert "ASD-STE100" in arch_prompt.split("</instructions>")[0]
    assert all(opts.local.custom_tools is None and opts.tools == [] for _, opts in fake.calls)


async def test_rerun_preserves_human_content(repo, settings, init_settings, mock_cursor):
    mock_cursor(responder)
    (repo / "platform").mkdir()
    (repo / "platform/AGENTS.md").write_text("# Team notes\n\nKeep this line.\n", encoding="utf-8")
    await _run(repo, settings, init_settings)
    text = (repo / "platform/AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# Team notes") and "Keep this line." in text and BEGIN in text

    await _run(repo, settings, init_settings)                              # second run: block replaced, not duplicated
    text = (repo / "platform/AGENTS.md").read_text(encoding="utf-8")
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
    result = await arun_init(req, llm=GenericAgentModel(settings=settings), settings=init_settings)
    assert not (repo / "platform").exists()
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
    result = await arun_init({"feature_id": "F"}, llm=GenericAgentModel(settings=settings), settings=s)
    assert result.output["pushed"] is True and result.output["branch"] == "shipAI"
    files = _git(remote, "ls-tree", "-r", "--name-only", "shipAI").splitlines()
    assert "platform/init/INIT.md" in files and "platform/AGENTS.md" in files
    changed = _git(remote, "diff", "--name-only", "main", "shipAI").splitlines()
    assert changed and all(f.startswith("platform/") for f in changed)


# --------------------------------------------------------------------------- units

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
    for bad in ("../x.md", "/etc/x.md", "C:/x.md", "src/app.ts", "skills/Bad Name/SKILL.md", "notes.md"):
        r = write_file(tmp_path, "platform", PlannedFile(path=bad, content="x"), dry_run=False)
        assert r.action == "failed", bad
    ok = write_file(tmp_path, "platform", PlannedFile(path="skills/run-tests/SKILL.md", content="# x"), False)
    assert ok.action == "created"


def test_check_links(tmp_path):
    (tmp_path / "platform").mkdir()
    (tmp_path / "platform/A.md").write_text("[ok](B.md) [bad](C.md) [web](https://x.y)", encoding="utf-8")
    (tmp_path / "platform/B.md").write_text("", encoding="utf-8")
    assert check_links(tmp_path, ["platform/A.md"]) == ["platform/A.md -> C.md"]


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

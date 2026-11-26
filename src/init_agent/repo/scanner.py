"""scan_repository: deterministic inventory of a local working copy (no LLM)."""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "out", ".angular", ".next", ".nuxt", "coverage",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".turbo", ".cache",
    "vendor", "target", "bin", "obj", ".idea", ".gradle", "workspaces",
}
TEXT_EXT = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java", ".kt", ".go", ".rs", ".rb", ".php",
    ".cs", ".swift", ".scala", ".c", ".h", ".cpp", ".hpp", ".vue", ".svelte", ".html", ".css", ".scss",
    ".less", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md", ".mdx", ".txt", ".sql", ".graphql",
    ".proto", ".sh", ".ps1", ".xml", ".gradle", ".properties", ".env.example", ".mdc",
}
TEXT_NAMES = {"Dockerfile", "Makefile", "Procfile", "Gemfile", "Rakefile", ".editorconfig", ".nvmrc",
              ".cursorrules", ".gitignore", ".npmrc", "CODEOWNERS", "justfile"}
MAX_SCAN_BYTES = 400_000

MANIFESTS = {"package.json", "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "go.mod", "Cargo.toml",
             "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "Pipfile", "*.csproj"}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb", "poetry.lock",
             "uv.lock", "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock", "Pipfile.lock"}
BUILD_CONFIG = {"angular.json", "tsconfig.json", "tsconfig.*.json", "vite.config.*", "webpack.config.*",
                "next.config.*", "nuxt.config.*", "vitest.config.*", "jest.config.*", "karma.conf.*",
                "playwright.config.*", "cypress.config.*", "babel.config.*", "Dockerfile", "docker-compose*.yml",
                "Makefile", "justfile", "eslint.config.*", ".eslintrc*", ".prettierrc*", "biome.json",
                "tox.ini", "pytest.ini", "langgraph.json"}
ENTRY_NAMES = {"main.ts", "main.tsx", "index.ts", "index.tsx", "main.js", "index.js", "app.ts", "server.ts",
               "server.js", "main.py", "app.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py", "main.go",
               "Program.cs", "Application.java", "app.config.ts", "app.routes.ts", "routes.ts", "router.ts"}
GUIDANCE_PATTERNS = ["AGENTS.md", "**/AGENTS.md", "CLAUDE.md", "**/CLAUDE.md", ".cursorrules", ".cursor/rules/*",
                     ".github/copilot-instructions.md", ".windsurfrules", "CONTRIBUTING.md", "ARCHITECTURE.md",
                     "docs/**/*.md", "adr/**/*.md", "**/SKILL.md", ".claude/**/*.md", ".cursor/**/*.md"]
TOOL_CONFIG = [".mcp.json", ".cursor/mcp.json", ".vscode/*.json", ".claude/*.json", ".devcontainer/*"]
HOOK_PATTERNS = [".husky/*", ".pre-commit-config.yaml", "lefthook.yml", "lefthook.yaml", ".lintstagedrc*",
                 "lint-staged.config.*", ".githooks/*", ".claude/settings.json", ".cursor/hooks.json"]
CI_PATTERNS = [".github/workflows/*", ".gitlab-ci.yml", "azure-pipelines.yml", ".circleci/*", "Jenkinsfile",
               "bitbucket-pipelines.yml", ".buildkite/*"]
TEST_RE = re.compile(r"(\.spec\.|\.test\.|_test\.|^test_|/tests?/|/__tests__/|/e2e/|/cypress/|/playwright/)")
CONTAINER_DIRS = {"src", "app", "apps", "lib", "libs", "packages", "services", "modules", "features", "pkg",
                  "internal", "cmd", "source", "main", "java", "python", "core", "shared"}


@dataclass
class FileEntry:
    path: str
    size: int
    category: str
    component: str


@dataclass
class Inventory:
    root: str
    version: dict = field(default_factory=dict)
    files: list[FileEntry] = field(default_factory=list)
    components: dict[str, list[str]] = field(default_factory=dict)
    manifests: list[str] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    build_config: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    ci: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    guidance: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    tool_config: list[str] = field(default_factory=list)
    existing_harness: list[str] = field(default_factory=list)
    package_scripts: dict[str, dict[str, str]] = field(default_factory=dict)
    readme_commands: list[dict] = field(default_factory=list)
    skipped_large: list[str] = field(default_factory=list)
    docs_chars: int = 0

    def paths(self) -> set[str]:
        return {f.path for f in self.files}

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_text(self, max_files: int = 400) -> str:
        """Compact inventory for prompts."""
        lines = [f"version: {json.dumps(self.version)}", f"files scanned: {len(self.files)}"]
        for name in ("manifests", "lockfiles", "build_config", "entry_points", "ci", "guidance", "hooks",
                     "tool_config", "existing_harness"):
            values = getattr(self, name)
            if values:
                lines.append(f"{name}: {', '.join(values[:60])}")
        if self.package_scripts:
            lines.append(f"package scripts: {json.dumps(self.package_scripts)}")
        lines.append(f"tests: {len(self.tests)} files, e.g. {', '.join(self.tests[:15])}")
        lines.append("components:")
        for comp, files in self.components.items():
            lines.append(f"  - {comp} ({len(files)} files)")
        lines.append("file list:")
        lines += [f"  {f.path}" for f in self.files[:max_files]]
        if len(self.files) > max_files:
            lines.append(f"  ... {len(self.files) - max_files} more")
        return "\n".join(lines)


def _match(path: str, patterns) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def _is_text(p: Path) -> bool:
    return p.suffix.lower() in TEXT_EXT or p.name in TEXT_NAMES or p.name.startswith(".env.example")


def component_of(path: str) -> str:
    """src/app/features/article/pages/x.ts -> src/app/features/article ; README.md -> (root)."""
    parts = PurePosixPath(path).parts
    if len(parts) == 1:
        return "(root)"
    dirs = parts[:-1]
    out: list[str] = []
    for d in dirs:
        out.append(d)
        if d.lower() not in CONTAINER_DIRS:
            break
    return "/".join(out)


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


_CMD_RE = re.compile(r"^\s*(?:\$\s*)?((?:npm|npx|yarn|pnpm|bun|bunx|python|python3|pip|uv|poetry|pytest|make|go|cargo|"
                     r"mvn|gradle|\./gradlew|dotnet|docker|docker-compose|ng|deno)\b[^\n`]*)$")


def _readme_commands(root: Path, docs: list[str]) -> list[dict]:
    found: list[dict] = []
    for rel in [d for d in docs if PurePosixPath(d).name.lower().startswith(("readme", "contributing", "development"))][:10]:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        in_code = False
        for line in text.splitlines():
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                m = _CMD_RE.match(line)
                if m:
                    cmd = m.group(1).split(" #")[0].strip()
                    if cmd and all(c["command"] != cmd for c in found):
                        found.append({"command": cmd, "source": rel, "status": "documented"})
    return found[:40]


def scan_repository(root: Path, include: list[str] | None = None, exclude: list[str] | None = None,
                    platform_dir: str = "platform") -> Inventory:
    root = root.resolve()
    inv = Inventory(root=str(root))
    sha = _git(root, "rev-parse", "HEAD")
    inv.version = {
        "commit": sha or None,
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD") or None,
        "remote": re.sub(r"//[^@/]+@", "//", _git(root, "remote", "get-url", "origin")) or None,
        "dirty_files": len(_git(root, "status", "--porcelain").splitlines()) if sha else None,
    }

    for p in sorted(root.rglob("*")):
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts[:-1]) or not p.is_file():
            continue
        rel = "/".join(rel_parts)
        if include and not _match(rel, include):
            continue
        if exclude and _match(rel, exclude):
            continue
        if rel.startswith(f"{platform_dir}/"):
            if rel.endswith(".md"):
                inv.existing_harness.append(rel)
            continue
        if p.name in LOCKFILES:                        # recorded, never sent to the model
            inv.lockfiles.append(rel)
            continue
        if not _is_text(p) and not _match(rel, HOOK_PATTERNS + CI_PATTERNS + GUIDANCE_PATTERNS + TOOL_CONFIG):
            continue
        size = p.stat().st_size
        if size > MAX_SCAN_BYTES:
            inv.skipped_large.append(rel)
            continue

        name = p.name
        if _match(rel, GUIDANCE_PATTERNS):
            category = "guidance"
            inv.guidance.append(rel)
        elif _match(rel, MANIFESTS):
            category = "manifest"
            inv.manifests.append(rel)
        elif _match(rel, CI_PATTERNS):
            category = "ci"
            inv.ci.append(rel)
        elif _match(rel, HOOK_PATTERNS):
            category = "hook"
            inv.hooks.append(rel)
        elif _match(rel, TOOL_CONFIG):
            category = "tool_config"
            inv.tool_config.append(rel)
        elif _match(rel, BUILD_CONFIG):
            category = "build_config"
            inv.build_config.append(rel)
        elif TEST_RE.search("/" + rel):
            category = "test"
            inv.tests.append(rel)
        elif p.suffix.lower() in (".md", ".mdx", ".txt"):
            category = "doc"
            inv.docs.append(rel)
            inv.docs_chars += size
        else:
            category = "source"
        if name in ENTRY_NAMES and category == "source":
            inv.entry_points.append(rel)

        comp = component_of(rel)
        inv.files.append(FileEntry(path=rel, size=size, category=category, component=comp))
        if category in ("source", "test"):
            inv.components.setdefault(comp, []).append(rel)

    # git hooks that are actually installed (not samples)
    hooks_dir = root / ".git" / "hooks"
    if hooks_dir.is_dir():
        inv.hooks += [f".git/hooks/{h.name}" for h in hooks_dir.iterdir() if h.is_file() and not h.name.endswith(".sample")]

    for manifest in inv.manifests:
        if PurePosixPath(manifest).name == "package.json":
            try:
                scripts = json.loads((root / manifest).read_text(encoding="utf-8")).get("scripts") or {}
                if scripts:
                    inv.package_scripts[manifest] = scripts
            except (json.JSONDecodeError, OSError):
                pass
    inv.readme_commands = _readme_commands(root, inv.docs + inv.guidance)
    return inv

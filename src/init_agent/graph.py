"""INIT LangGraph:

START -> prepare_workspace -> scan_repository -> analyze_repository -> derive_skills
      -> assess_automation -> plan_harness -> assess_coverage -> apply_harness
      -> repair_links -> write_init_report -> publish_branch -> END

LLM nodes call `GenericAgent.run()` (generic_agent.runner) on the instance INIT creates,
with the node's instructions, rules, guidelines and skills from the prompt pack.
Every other step is deterministic Python.
"""

from __future__ import annotations

import asyncio
import json
import operator
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from .config import InitSettings
from .llm import GenericAgent, ainvoke_structured, ainvoke_text
from .models import (
    Alert, AutomationResult, CommandInfo, ComponentAnalysis, HarnessPlan, HookProposal, InitRequest, PlannedFile,
    RepoAnalysis, SkillSpec, SkillsResult, WriteResult,
)
from .prompt_pack import PromptPack
from .render import link, render_hooks, render_report, render_skill, ste_sentence_check
from .repo.excerpts import evidence_block, pack, read_excerpt
from .repo.git_workspace import GitError, GitWorkspace
from .repo.scanner import FileEntry, Inventory, scan_repository
from .repo.writer import (
    PathRejected, broken_targets, check_links, code_folder_of, generated_block, is_generated_only, is_harness_path,
    is_local_agents, is_skill_path, normalize, replace_generated_block, unlink, validate, write_file,
)


def hub_folders(components: list[str], root: Path, min_components: int) -> dict[str, list[str]]:
    """Folders (not the root) that hold at least `min_components` components below them."""
    comps = sorted(c for c in components if c != "(root)")
    folders = {"/".join(PurePosixPath(c).parts[:i]) for c in comps for i in range(1, len(PurePosixPath(c).parts))}
    out: dict[str, list[str]] = {}
    for f in sorted(folders):
        inside = [c for c in comps if c.startswith(f + "/")]
        if len(inside) >= max(1, min_components) and (root / f).is_dir():
            out[f] = inside
    return out


def skill_folder(scope: list[str], local_folders: set[str], root: Path) -> str | None:
    """Deepest folder with a local AGENTS.md that holds the whole scope of a skill; None = root `skills/`."""
    bases: list[tuple[str, ...]] = []
    for s in scope:
        parts: list[str] = []
        for part in PurePosixPath(s.strip().lstrip("./").rstrip("/")).parts:
            if any(ch in part for ch in "*?[{"):
                break
            parts.append(part)
        if parts and (root / "/".join(parts)).is_file():
            parts.pop()                                     # a file: its folder is the base
        bases.append(tuple(parts))
    if not bases:
        return None
    common: list[str] = []
    for level in zip(*bases):
        if len(set(level)) != 1:
            break
        common.append(level[0])
    base = "/".join(common)
    homes = [f for f in local_folders if base == f or base.startswith(f + "/")]
    return max(homes, key=len) if homes else None


class InitState(TypedDict, total=False):
    run_id: str
    request: dict
    repo_root: str
    repo_url: str
    base_ref: str
    base_sha: str
    branch: str
    cloned: bool
    inventory: dict
    analysis: dict
    skills: dict
    automation: dict
    plan: dict
    coverage: dict
    writes: list[dict]
    repairs: dict
    report_path: str
    published: dict
    prompt_ids: dict
    alerts: Annotated[list[dict], operator.add]
    progress: Annotated[list[str], operator.add]


ProgressFn = Callable[[str, str], None]


def build_init_graph(agent: GenericAgent, settings: InitSettings, prompt_pack: PromptPack,
                     on_progress: ProgressFn | None = None):
    sem = asyncio.Semaphore(max(1, settings.llm_concurrency))
    pdir = settings.platform_dir

    def progress(node: str, msg: str) -> list[str]:
        if on_progress:
            on_progress(node, msg)
        return [f"{node}: {msg}"]

    def req(state: InitState) -> InitRequest:
        return InitRequest.model_validate(state["request"])

    async def call_json(state: InitState, node: str, task: str, schema: type[BaseModel]) -> Any:
        r = req(state)
        run_input = prompt_pack.run_input(node, platform_dir=pdir, extra_rules=r.inputs.extra_rules,
                                          extra_guidelines=r.inputs.extra_guidelines,
                                          shared_skills=r.inputs.shared_skills)
        async with sem:
            return await ainvoke_structured(agent, task, schema, retries=settings.json_retries,
                                            metadata={"init_run_id": state["run_id"], "init_node": node},
                                            **run_input)

    async def call_markdown(state: InitState, file_path: str, task: str, node: str = "draft_file") -> str:
        r = req(state)
        run_input = prompt_pack.run_input(node, platform_dir=pdir, file_path=file_path,
                                          extra_rules=r.inputs.extra_rules,
                                          extra_guidelines=r.inputs.extra_guidelines,
                                          shared_skills=r.inputs.shared_skills)
        async with sem:
            return await ainvoke_text(agent, task,
                                      metadata={"init_run_id": state["run_id"], "init_node": node},
                                      **run_input)

    def link_table(from_path: str, targets: list[tuple[str, str]]) -> str:
        """Exact relative link from `from_path` to each target: the model copies these."""
        return "\n".join(f"- `{p}`{' (' + what + ')' if what else ''}: link target `{link(from_path, p)}`"
                         for p, what in targets if p != from_path)

    def operator_context(r: InitRequest) -> str:
        parts = []
        if r.inputs.project_brief:
            parts.append(evidence_block("operator:project_brief", r.inputs.project_brief))
        for i, doc in enumerate(r.inputs.documents, 1):
            parts.append(evidence_block(f"operator:document-{i}", doc[: settings.max_file_chars]))
        if r.inputs.mock_repo_scan:
            parts.append(evidence_block("operator:sample_scan_output", json.dumps(r.inputs.mock_repo_scan, indent=1)))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ 0. prepare_workspace
    def prepare_workspace(state: InitState) -> dict:
        r = req(state)
        if r.project.repo_path:                       # local working copy (dev/tests): no clone, no push
            root = Path(r.project.repo_path).resolve()
            if not root.is_dir():
                raise FileNotFoundError(f"repo_path does not exist: {root}")
            return {"repo_root": str(root), "cloned": False, "branch": "",
                    "progress": progress("prepare_workspace", f"using local copy {root}")}
        url = r.project.repo_url or settings.repo_url
        base = r.project.ref or settings.repo_base_ref
        ws = GitWorkspace.prepare(url, base, settings.work_branch, Path(settings.workspace_dir), state["run_id"],
                                  push_url=settings.push_url, token=settings.git_token.get_secret_value())
        return {"repo_root": str(ws.path), "repo_url": url, "base_ref": base, "base_sha": ws.base_sha,
                "branch": ws.branch, "cloned": True,
                "progress": progress("prepare_workspace", f"cloned {url}@{base} -> branch {ws.branch}")}

    # ------------------------------------------------------------------ 1. scan_repository
    def scan(state: InitState) -> dict:
        r = req(state)
        inv = scan_repository(Path(state["repo_root"]), r.inputs.scan_scope.include, r.inputs.scan_scope.exclude, pdir)
        # Harness files live in the repository itself (root and code folders), so earlier
        # INIT output is found among the scanned files.
        inv.existing_harness = sorted(f.path for f in inv.files if is_harness_path(f.path))
        return {"inventory": inv.to_dict(),
                "progress": progress("scan_repository", f"{len(inv.files)} files, {len(inv.components)} components")}

    # ------------------------------------------------------------------ 2. analyze_repository
    async def analyze(state: InitState) -> dict:
        r = req(state)
        inv = state["inventory"]
        root = Path(state["repo_root"])
        inv_obj = Inventory(**{**inv, "files": [FileEntry(**f) for f in inv["files"]]})
        summary = inv_obj.summary_text()
        alerts: list[dict] = []

        # overview: manifests, build config, top-level docs, guidance, CI
        top_docs = [d for d in inv["docs"] if "/" not in d or d.lower().startswith("docs/")][:6]
        overview_paths = inv["manifests"] + inv["build_config"][:12] + top_docs + inv["guidance"][:12] + inv["ci"][:4]
        ov = pack(root, overview_paths, settings.context_budget_chars, settings.max_file_chars, 1)
        task = (f"Node: analyze_overview.\n\n## Inventory\n{summary}\n\n## Operator input\n{operator_context(r)}\n\n"
                f"## Evidence\n{ov.chunks[0].text if ov.chunks else '(none)'}")
        try:
            overview: RepoAnalysis = await call_json(state, "analyze_overview", task, RepoAnalysis)
        except Exception as exc:  # noqa: BLE001
            overview = RepoAnalysis()
            alerts.append(Alert(kind="llm", severity="error", reason=f"overview analysis failed: {exc}").model_dump())

        # components, largest first
        comps = sorted(inv["components"].items(), key=lambda kv: -len(kv[1]))
        selected, skipped = comps[: settings.max_components], comps[settings.max_components:]
        left_out: list[str] = list(ov.left_out)
        truncated: list[str] = list(ov.truncated)

        async def one(name: str, paths: list[str]) -> ComponentAnalysis | None:
            packing = pack(root, paths, settings.context_budget_chars, settings.max_file_chars,
                           settings.max_calls_per_component)
            left_out.extend(packing.left_out)
            truncated.extend(packing.truncated)
            merged = ComponentAnalysis(component=name)
            for i, chunk in enumerate(packing.chunks, 1):
                t = (f"Node: analyze_repository. Component `{name}` (part {i}/{len(packing.chunks)}).\n"
                     f"Files of this component not included in any part: {packing.left_out or 'none'}.\n\n"
                     f"## System summary\n{overview.summary}\n\n## Inventory\n{summary}\n\n## Evidence\n{chunk.text}")
                try:
                    part: ComponentAnalysis = await call_json(state, "analyze_repository", t, ComponentAnalysis)
                except Exception as exc:  # noqa: BLE001
                    alerts.append(Alert(kind="llm", severity="warning", affected_scope=[name],
                                        reason=f"analysis of {name} part {i} failed: {exc}").model_dump())
                    continue
                for f in ("business_behavior", "responsibilities", "entry_points", "interfaces", "dependencies",
                          "data_flow", "coverage_gaps", "needs_more_evidence"):
                    getattr(merged, f).extend(getattr(part, f))
            return merged

        results = await asyncio.gather(*(one(n, p) for n, p in selected))
        overview.components = [c for c in results if c is not None]
        coverage = {"left_out": sorted(set(left_out)), "truncated": sorted(set(truncated)),
                    "not_analyzed": [n for n, _ in skipped]}
        return {"analysis": overview.model_dump(), "coverage": coverage, "alerts": alerts,
                "prompt_ids": {"analyze_overview": prompt_pack.ids_for("analyze_overview"),
                               "analyze_repository": prompt_pack.ids_for("analyze_repository")},
                "progress": progress("analyze_repository", f"{len(overview.components)} components analyzed")}

    def compact_analysis(state: InitState) -> str:
        return json.dumps(state.get("analysis") or {}, ensure_ascii=False)[: settings.context_budget_chars // 2]

    # ------------------------------------------------------------------ 3. derive_skills
    async def derive(state: InitState) -> dict:
        inv = state["inventory"]
        root = Path(state["repo_root"])
        examples = pack(root, inv["tests"][:4] + [g for g in inv["guidance"] if g.endswith("SKILL.md")][:4],
                        settings.context_budget_chars // 3, settings.max_file_chars // 2, 1)
        earlier = sorted({PurePosixPath(p).parent.name for p in inv.get("existing_harness", []) if is_skill_path(p)})
        shared = [s.id for s in req(state).inputs.shared_skills]
        task = (f"Node: derive_skills.\n\n"
                f"## Shared skill catalog ids (the only valid `shared_skill_id` values)\n{shared or 'none'}\n\n"
                f"## Skills INIT wrote in an earlier run\n{earlier or 'none'}\nThese are local skills of this "
                f"repository, not shared skills. Return them again as `local` (same name) when the evidence "
                f"still supports them.\n\n## Analysis\n{compact_analysis(state)}\n\n"
                f"## Tests ({len(inv['tests'])})\n{inv['tests'][:80]}\n\n## Package scripts\n{json.dumps(inv['package_scripts'])}\n\n"
                f"## Documented commands\n{json.dumps(inv['readme_commands'])}\n\n## Guidance files\n{inv['guidance']}\n\n"
                f"## Evidence\n{examples.chunks[0].text if examples.chunks else '(none)'}")
        try:
            result: SkillsResult = await call_json(state, "derive_skills", task, SkillsResult)
        except Exception as exc:  # noqa: BLE001
            return {"skills": {"skills": []},
                    "alerts": [Alert(kind="llm", severity="warning", reason=f"derive_skills failed: {exc}").model_dump()],
                    "progress": progress("derive_skills", "failed, continuing without skills")}
        # `shared_ref` is valid only for an id from the operator's shared catalog. Anything else
        # (for example a SKILL.md that INIT wrote in an earlier run) is a local skill.
        catalog = {s.id for s in req(state).inputs.shared_skills}
        seen, skills = set(), []
        for s in result.skills:
            s.name = "".join(ch if ch.isalnum() else "-" for ch in s.name.lower()).strip("-")[:60] or "skill"
            if s.kind == "shared_ref" and (s.shared_skill_id or s.name) not in catalog:
                s.kind = "local" if s.steps else "needs_evidence"
                s.shared_skill_id = None
                if s.kind == "needs_evidence" and not s.missing_evidence:
                    s.missing_evidence = ["steps (marked shared, but no shared skill has this id)"]
            if s.name not in seen:
                seen.add(s.name)
                skills.append(s)
        return {"skills": {"skills": [s.model_dump() for s in skills]},
                "progress": progress("derive_skills", f"{sum(s.kind == 'local' for s in skills)} local, "
                                                     f"{sum(s.kind == 'shared_ref' for s in skills)} shared")}

    # ------------------------------------------------------------------ 4. assess_automation
    async def automation(state: InitState) -> dict:
        r = req(state)
        inv = state["inventory"]
        root = Path(state["repo_root"])
        ev = pack(root, [h for h in inv["hooks"] if not h.startswith(".git/")] + inv["ci"],
                  settings.context_budget_chars // 2, settings.max_file_chars // 2, 1)
        executed: list[CommandInfo] = []
        for cmd in r.inputs.verify_commands:                   # only operator-approved commands run
            try:
                proc = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=900)
                status = "executed_passed" if proc.returncode == 0 else "executed_failed"
                purpose = f"exit code {proc.returncode}"
            except subprocess.TimeoutExpired:
                status, purpose = "executed_failed", "timed out after 900s"
            executed.append(CommandInfo(command=cmd, purpose=purpose, source="operator", status=status))
        task = (f"Node: assess_automation.\n\n## Detected hooks\n{inv['hooks']}\n\n## CI\n{inv['ci']}\n\n"
                f"## Package scripts\n{json.dumps(inv['package_scripts'])}\n\n"
                f"## Documented commands\n{json.dumps(inv['readme_commands'])}\n\n"
                f"## Execution results supplied by the caller\n{json.dumps([c.model_dump() for c in executed]) or 'none'}\n\n"
                f"## Evidence\n{ev.chunks[0].text if ev.chunks else '(none)'}")
        try:
            result: AutomationResult = await call_json(state, "assess_automation", task, AutomationResult)
        except Exception as exc:  # noqa: BLE001
            result = AutomationResult(notes=[f"automation analysis failed: {exc}"])
        existing = set(inv["hooks"]) | set(inv["ci"])
        for h in result.hooks:                                  # R7 enforced in code
            if h.status == "active" and not (set(h.evidence) & existing):
                h.status = "proposed"
        by_cmd = {c.command: c for c in result.verification_commands}
        for c in executed:
            by_cmd[c.command] = c
        for c in by_cmd.values():                               # R3 enforced in code
            if c.status != "documented" and c.command not in {e.command for e in executed}:
                c.status = "documented"
        result.verification_commands = list(by_cmd.values())
        return {"automation": result.model_dump(),
                "progress": progress("assess_automation", f"{len(result.hooks)} hooks, "
                                                          f"{len(result.verification_commands)} commands")}

    # ------------------------------------------------------------------ 5. plan_harness
    async def plan(state: InitState) -> dict:
        inv = state["inventory"]
        root = Path(state["repo_root"])
        skills = state.get("skills", {}).get("skills", [])
        auto = state.get("automation") or {}
        existing = set(inv["existing_harness"])
        hubs = hub_folders(list(inv["components"]), root, settings.local_agents_min_components)
        hub_lines = "\n".join(f"- `{h}/AGENTS.md`: {len(c)} components ({', '.join(c)})" for h, c in hubs.items())
        task = (f"Node: plan_harness.\n\nHarness location: the repository root and the code folders "
                f"(paths are repository-relative). Existing harness files: {sorted(existing) or 'none'}\n\n"
                f"## Required local AGENTS.md (folders with {settings.local_agents_min_components} or more "
                f"components; the caller adds them if you leave them out)\n{hub_lines or 'none'}\n\n"
                f"## Analysis\n{compact_analysis(state)}\n\n## Skills\n{json.dumps(skills)[:8000]}\n\n"
                f"## Automation\n{json.dumps(auto)[:6000]}\n\n## Existing guidance files\n{inv['guidance']}\n\n"
                f"## Components\n{list(inv['components'])}")
        try:
            hp: HarnessPlan = await call_json(state, "plan_harness", task, HarnessPlan)
        except Exception as exc:  # noqa: BLE001
            hp = HarnessPlan(coverage_gaps=[f"plan_harness failed: {exc}"])

        files: dict[str, PlannedFile] = {}
        for f in hp.files:
            f.path = normalize(f.path, pdir)
            if is_skill_path(f.path) or f.path == "HOOKS.md":
                continue                                        # rendered deterministically below
            if (f.path in existing or (root / f.path).is_file()) and f.action == "create":
                f.action = "update"
            files[f.path] = f
        for base, purpose in (("AGENTS.md", "Entry point for agents"), ("ARCHITECTURE.md", "Current system by component"),
                              ("RULES.md", "Binding constraints with their basis")):
            files.setdefault(base, PlannedFile(path=base, scope="repository", purpose=purpose,
                                               action="update" if (root / base).is_file() else "create"))

        # Every folder with enough components gets a local AGENTS.md (enforced, not only asked).
        for hub, comps in hubs.items():
            p = f"{hub}/AGENTS.md"
            files.setdefault(p, PlannedFile(path=p, scope=hub, purpose=f"Local guide for {len(comps)} components: "
                                            + ", ".join(c[len(hub) + 1:] for c in comps)[:200],
                                            action="update" if (root / p).is_file() else "create"))

        # Folders that get a local AGENTS.md are the homes for local skills.
        local_folders: set[str] = set()
        for p in files:
            if is_local_agents(p):
                try:
                    validate(p, root, pdir)
                    local_folders.add(code_folder_of(p))
                except PathRejected:
                    pass

        repo_paths = {f["path"] for f in inv["files"]}
        for s in skills:
            if s["kind"] == "local":
                home = skill_folder(s.get("scope") or [], local_folders, root)
                p = f"{home}/skills/{s['name']}/SKILL.md" if home else f"skills/{s['name']}/SKILL.md"
                files[p] = PlannedFile(path=p, scope=", ".join(s.get("scope") or []) or "repository",
                                       action="update" if (root / p).is_file() else "create",
                                       purpose=f"Skill: {s['when_to_use'][:120]}", sources=s.get("sources") or [],
                                       content=render_skill(SkillSpec(**s), p, repo_paths))
        if auto.get("hooks") or auto.get("verification_commands"):
            files["HOOKS.md"] = PlannedFile(
                path="HOOKS.md", scope="repository", purpose="Active and proposed hooks, verification commands",
                action="update" if (root / "HOOKS.md").is_file() else "create",
                sources=sorted({e for h in auto.get("hooks", []) for e in h.get("evidence", [])}),
                content=render_hooks([HookProposal(**h) for h in auto.get("hooks", [])],
                                     [CommandInfo(**c) for c in auto.get("verification_commands", [])]))

        manifest = "\n".join(f"- `{f.path}` ({f.action}): {f.purpose}" for f in files.values())
        # Only targets the writer will accept go into the link table.
        linkable: list[tuple[str, str]] = []
        for f in files.values():
            try:
                validate(f.path, root, pdir)
                linkable.append((f.path, f.purpose[:80]))
            except PathRejected:
                pass
        linkable.append((settings.report_path, "INIT report"))

        async def draft(f: PlannedFile) -> None:
            if f.content is not None or f.action == "keep":
                return
            extra = ""
            if is_local_agents(f.path):
                folder = f.path[:-len("/AGENTS.md")]
                local = [p for p in repo_paths if p.startswith(folder + "/")]
                ex = pack(root, local, settings.context_budget_chars // 2, settings.max_file_chars // 2, 1)
                extra = ex.chunks[0].text if ex.chunks else ""
                inner = hubs.get(folder)
                own = [p for p in files if is_skill_path(p) and code_folder_of(p) == folder]
                extra = ((f"Components inside this folder: {inner}\n" if inner else "")
                         + ("Skills that live in this folder (list them first under `## Use these skills`): "
                            + ", ".join(f"`{p}`" for p in own) + "\n" if own else "")
                         + "\n" + extra)
            if (root / f.path).is_file():
                text, _ = read_excerpt(root, f.path, settings.max_file_chars)
                extra += "\n\n" + evidence_block(f.path, text) + (
                    "\nThe file above exists. Write only the generated part. Do not repeat its human-authored content.")
            depth = len(PurePosixPath(f.path).parts) - 1
            where = ("It sits at the repository root: link to repository files with their repository path."
                     if not depth else
                     f"It sits in `{PurePosixPath(f.path).parent}/`: links are relative to that folder "
                     f"(files at the repository root start with `{'../' * depth}`).")
            sources = [(s, "source") for s in f.sources if s in repo_paths]
            t = (f"Write the Markdown body of `{f.path}`.\nScope: {f.scope}\nPurpose: {f.purpose}\n"
                 f"Sources: {f.sources}\n{where}\n\n"
                 f"## Link table (copy link targets exactly; any other file: name it in `code`, no link)\n"
                 f"{link_table(f.path, linkable + sources)}\n\n"
                 f"## Planned harness files\n{manifest}\n\n## Analysis\n{compact_analysis(state)}\n\n"
                 f"## Skills\n{json.dumps(skills)[:6000]}\n\n## Automation\n{json.dumps(auto)[:4000]}\n\n"
                 f"## Evidence\n{extra or '(see analysis)'}")
            try:
                f.content = await call_markdown(state, f.path, t)
            except Exception as exc:  # noqa: BLE001
                hp.coverage_gaps.append(f"drafting {f.path} failed: {exc}")

        await asyncio.gather(*(draft(f) for f in files.values()))
        hp.files = list(files.values())
        ids = {f.path: prompt_pack.ids_for("draft_file", f.path) for f in hp.files if f.content is not None}
        return {"plan": hp.model_dump(),
                "prompt_ids": {**(state.get("prompt_ids") or {}), "plan_harness": prompt_pack.ids_for("plan_harness"),
                               "draft_file": ids},
                "progress": progress("plan_harness", f"{len(hp.files)} files planned")}

    # ------------------------------------------------------------------ 6. assess_coverage
    def coverage(state: InitState) -> dict:
        r = req(state)
        inv = state["inventory"]
        cov = dict(state.get("coverage") or {})
        analysis = state.get("analysis") or {}
        hp = state.get("plan") or {}
        gaps: list[str] = []
        need: list[str] = []
        for c in analysis.get("components", []):
            gaps += [f"{c['component']}: {g}" for g in c.get("coverage_gaps", [])]
            need += c.get("needs_more_evidence", [])
        gaps += hp.get("coverage_gaps", [])
        gaps += [f"component not analyzed (limit INIT_MAX_COMPONENTS): {n}" for n in cov.get("not_analyzed", [])]
        cov["gaps"] = gaps

        alerts: list[Alert] = []
        reasons, scope, material = [], [], []
        left = set(cov.get("left_out", []))
        hard = [n for n in need if n in left or any(n.startswith(x) for x in left)]
        if hard:
            reasons.append("evidence the model asked for stayed outside the context budget after the split by component")
            scope += sorted({p.split("/")[0] for p in hard})
            material += hard[:20]
        if inv.get("docs_chars", 0) > settings.docs_rag_threshold_chars:
            reasons.append(f"documentation is large ({inv['docs_chars']} chars) and scattered; repeated retrieval is likely")
            material.append("docs/**")
        if r.inputs.retrieval_gaps:
            reasons.append("the operator reported retrieval gaps")
            material += r.inputs.retrieval_gaps
        rag = hp.get("rag") or {}
        if rag.get("recommended"):
            reasons.append(rag.get("reason") or "the model recommended RAG")
            scope += rag.get("affected_scope", [])
            material += rag.get("material_to_index", [])
        if reasons:
            alerts.append(Alert(kind="rag", severity="warning", affected_scope=sorted(set(scope)) or ["repository"],
                                reason="; ".join(reasons), missing_for=rag.get("missing_for") or ["Requirements", "Architect"],
                                suggested_material=list(dict.fromkeys(material))))
        if len(left) > 0 and not hard:
            alerts.append(Alert(kind="coverage", severity="info", affected_scope=sorted({p.split('/')[0] for p in left}),
                                reason=f"{len(left)} files were not sent to the model because of the context budget"))
        return {"coverage": cov, "alerts": [a.model_dump() for a in alerts],
                "progress": progress("assess_coverage", f"{len(gaps)} gaps, rag={'yes' if reasons else 'no'}")}

    # ------------------------------------------------------------------ 7. apply_harness
    def apply(state: InitState) -> dict:
        r = req(state)
        root = Path(state["repo_root"])
        hp = HarnessPlan.model_validate(state.get("plan") or {})
        writes = [write_file(root, pdir, f, r.dry_run) for f in hp.files]
        # A skill INIT wrote in an earlier run that this run writes at another path (it moved
        # next to its code) is removed from the old path, but only when the file holds nothing
        # but INIT's block. A skill this run did not derive again stays where it is: one model
        # answer is not enough reason to delete it.
        planned = {f.path for f in hp.files}
        planned_skills = {PurePosixPath(p).parent.name for p in planned if is_skill_path(p)}
        for old in state["inventory"].get("existing_harness", []):
            if not is_skill_path(old) or old in planned or PurePosixPath(old).parent.name not in planned_skills:
                continue
            target = root / old
            if not target.is_file() or not is_generated_only(target.read_text(encoding="utf-8")):
                continue
            if r.dry_run:
                writes.append(WriteResult(path=old, action="planned", detail="would remove: INIT skill no longer planned"))
                continue
            target.unlink()
            try:
                target.parent.rmdir()                       # the skill's own folder, when empty
            except OSError:
                pass
            writes.append(WriteResult(path=old, action="removed", detail="INIT skill moved next to its code"))
        alerts = [Alert(kind="write_failure", severity="error", affected_scope=[w.path], reason=w.detail).model_dump()
                  for w in writes if w.action == "failed"]
        for c in hp.conflicts:
            alerts.append(Alert(kind="conflict", severity="warning", reason=c).model_dump())
        return {"writes": [w.model_dump() for w in writes], "alerts": alerts,
                "progress": progress("apply_harness", f"{sum(w.action in ('created', 'updated') for w in writes)} written")}

    # ------------------------------------------------------------------ 7b. repair_links
    async def repair_links(state: InitState) -> dict:
        """Every written file ends with links that resolve.

        1. Check each generated block (human content outside it is never changed).
        2. A block with broken links goes back to the Generic Agent once, with the exact
           link table and candidates found on disk (skill `repair-links`).
        3. Links still broken after that become plain text, and the report lists them.
        """
        r = req(state)
        if r.dry_run:
            return {"repairs": {}, "progress": progress("repair_links", "skipped (dry run)")}
        root = Path(state["repo_root"])
        pending = [settings.report_path, settings.status_path]
        # Every INIT-managed file still on disk: written now, kept by the plan, or left from an
        # earlier run. A kept file can point at a skill that moved in this run.
        managed = {w["path"] for w in state.get("writes", []) if w["action"] in ("created", "updated", "kept")}
        managed |= set(state["inventory"].get("existing_harness", []))
        managed = sorted(p for p in managed if p.endswith(".md") and (root / p).is_file())
        on_disk = sorted(p for p in {f["path"] for f in state["inventory"]["files"]} | set(managed)
                         if (root / p).is_file())
        targets = [(p, "") for p in managed] + [(settings.report_path, "INIT report")]
        repaired: list[str] = []
        unlinked: list[str] = []
        moved: list[str] = []

        def same_file(target: str) -> list[str]:
            """Files on disk whose last two path parts match the broken target (a moved skill)."""
            parts = PurePosixPath(target.split("#")[0]).parts
            key = "/".join(p for p in parts[-2:] if p not in ("..", "."))
            return [p for p in on_disk if p == key or p.endswith("/" + key)] if "/" in key else []

        async def fix(path: str) -> None:
            file = root / path
            text = file.read_text(encoding="utf-8")
            block = generated_block(text)
            if block is None:
                return
            bad = broken_targets(root, path, block, pending)
            if not bad:
                return
            # 1. Deterministic: a target whose file now lives elsewhere (one exact match).
            new = block
            for t in bad:
                hits = same_file(t)
                if len(hits) == 1:
                    new = new.replace(f"]({t})", f"]({link(path, hits[0])})")
                    moved.append(f"{path}: {t} -> {link(path, hits[0])}")
            bad = broken_targets(root, path, new, pending)
            # 2. The Generic Agent, once, for what is left.
            if bad:
                candidates = []
                for t in bad:
                    name = PurePosixPath(t.split("#")[0]).name
                    stem = name.rsplit(".", 1)[0][:20]
                    near = (same_file(t) or [p for p in on_disk if PurePosixPath(p).name == name
                                             or (stem and stem in p and p.endswith(".md"))])[:5]
                    candidates.append(f"- `{t}` -> "
                                      + (", ".join(f"`{link(path, p)}`" for p in near) or "no candidate"))
                task = (f"Node: repair_links.\n\nFile: `{path}`\n\n## Broken links\n"
                        + "\n".join(f"- `{t}`" for t in bad)
                        + "\n\n## Candidates on disk (link targets from this file)\n" + "\n".join(candidates)
                        + f"\n\n## Link table (copy link targets exactly)\n{link_table(path, targets)}\n\n"
                        + f"## Current body\n{evidence_block(path, new)}")
                try:
                    answer = (await call_markdown(state, path, task, node="repair_links")).strip("\n")
                    # Accept a repair that keeps the body (not a summary or an empty reply).
                    if len(answer) >= 0.6 * len(new):
                        new = answer
                except Exception:  # noqa: BLE001 - fall back to unlinking below
                    pass
            # 3. Whatever is still broken becomes plain text.
            still = broken_targets(root, path, new, pending)
            if still:
                new = unlink(new, still)
                unlinked.extend(f"{path} -> {t}" for t in still)
            if new != block:
                file.write_text(replace_generated_block(text, new), encoding="utf-8", newline="\n")
                repaired.append(path)

        await asyncio.gather(*(fix(p) for p in managed))
        alerts = [Alert(kind="coverage", severity="info", affected_scope=[u.split(" -> ")[0]],
                        reason=f"link target not found, link removed: {u}").model_dump() for u in unlinked]
        writes = [dict(w) for w in state.get("writes", [])]
        known = {w["path"] for w in writes}
        # A kept or earlier file that INIT changed now is reported as updated, and committed.
        extra = [WriteResult(path=p, action="updated", detail="links repaired").model_dump()
                 for p in sorted(repaired) if p not in known]
        for w in writes:
            if w["path"] in repaired and w["action"] == "kept":
                w["action"], w["detail"] = "updated", "links repaired"
        return {"repairs": {"repaired": sorted(repaired), "moved": moved, "unlinked": unlinked},
                "writes": writes + extra, "alerts": alerts,
                "progress": progress("repair_links", f"{len(repaired)} files repaired ({len(moved)} moved targets), "
                                                     f"{len(unlinked)} links removed")}

    # ------------------------------------------------------------------ 8. write_init_report
    def report(state: InitState) -> dict:
        r = req(state)
        root = Path(state["repo_root"])
        writes = [WriteResult(**w) for w in state.get("writes", [])]
        written = [w.path for w in writes if w.action in ("created", "updated", "kept")]
        # The report is written below, after this check: links to it are not broken.
        broken = check_links(root, written, pending=[] if r.dry_run else [settings.report_path, settings.status_path])
        repo_paths = {f["path"] for f in state["inventory"]["files"]}
        hp = state.get("plan") or {}
        cited = {s for f in hp.get("files", []) for s in f.get("sources", [])}
        unknown = sorted(s for s in cited
                         if ("/" in s or "." in s) and s not in repo_paths
                         and not s.startswith(pdir) and not (root / s).exists())
        ste_files = [w for w in written if w.endswith("ARCHITECTURE.md") or is_local_agents(w)]
        long_sentences = []
        for p in ste_files:
            if (root / p).is_file():
                long_sentences += [f"`{p}`: {s}" for s in ste_sentence_check((root / p).read_text(encoding="utf-8"))]
        alerts = [Alert(**a) for a in state.get("alerts", [])]
        published = {"branch": state.get("branch"),
                     "pushed": "after this report" if state.get("cloned") and settings.push_enabled and not r.dry_run else False}
        md = render_report(state=state, writes=writes, alerts=alerts, broken_links=broken, unknown_evidence=unknown,
                           ste={"files": ste_files, "long_sentences": long_sentences}, platform_dir=pdir,
                           published=published if state.get("cloned") else {})
        if not r.dry_run:
            path = root / settings.report_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(md, encoding="utf-8", newline="\n")
            status_file = root / settings.status_path
            revision = 1
            if status_file.is_file():
                try:
                    revision = int(json.loads(status_file.read_text(encoding="utf-8")).get("revision", 0)) + 1
                except (json.JSONDecodeError, ValueError):
                    pass
            status_file.write_text(json.dumps({"feature_id": r.feature_id, "stage": "init", "status": "completed",
                                               "run_id": state["run_id"], "revision": revision}, indent=2) + "\n",
                                   encoding="utf-8", newline="\n")
        extra = [Alert(kind="coverage", severity="warning", reason=f"broken link: {b}").model_dump() for b in broken]
        return {"report_path": settings.report_path, "alerts": extra,
                "progress": progress("write_init_report", f"{settings.report_path} ({len(broken)} broken links)")}

    # ------------------------------------------------------------------ 9. publish_branch
    def publish(state: InitState) -> dict:
        r = req(state)
        if not state.get("cloned") or r.dry_run:
            return {"published": {"pushed": False, "reason": "local copy or dry run"},
                    "progress": progress("publish_branch", "skipped")}
        root = Path(state["repo_root"])
        ws = GitWorkspace(path=root, branch=state["branch"], base_ref=state["base_ref"], base_sha=state["base_sha"])
        try:
            harness = [w["path"] for w in state.get("writes", []) if w["action"] in ("created", "updated", "removed")]
            sha = ws.commit([pdir, *harness], f"INIT: agent harness in the repository (run {state['run_id']})",
                            settings.git_author_name, settings.git_author_email)
            pushed = False
            if sha and settings.push_enabled:
                ws.push(settings.git_token.get_secret_value(), settings.push_url)
                pushed = True
            return {"published": {"branch": ws.branch, "commit": sha, "pushed": pushed},
                    "progress": progress("publish_branch", f"commit {sha or 'none'} pushed={pushed}")}
        except GitError as exc:
            return {"published": {"branch": ws.branch, "pushed": False, "error": str(exc)},
                    "alerts": [Alert(kind="write_failure", severity="error", reason=f"publish failed: {exc}").model_dump()],
                    "progress": progress("publish_branch", "failed")}

    g = StateGraph(InitState)
    nodes = [("prepare_workspace", prepare_workspace), ("scan_repository", scan), ("analyze_repository", analyze),
             ("derive_skills", derive), ("assess_automation", automation), ("plan_harness", plan),
             ("assess_coverage", coverage), ("apply_harness", apply), ("repair_links", repair_links),
             ("write_init_report", report),
             ("publish_branch", publish)]
    for name, fn in nodes:
        g.add_node(name, fn)
    g.add_edge(START, nodes[0][0])
    for (a, _), (b, _) in zip(nodes, nodes[1:]):
        g.add_edge(a, b)
    g.add_edge(nodes[-1][0], END)
    return g.compile()

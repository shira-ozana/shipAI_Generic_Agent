"""Deterministic Markdown renderers: SKILL.md, HOOKS.md, INIT.md, and the STE100 length check."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from .models import Alert, CommandInfo, HookProposal, SkillSpec, WriteResult


def link(from_file: str, to_path: str) -> str:
    """Relative link between two repo-relative paths."""
    start = str(PurePosixPath(from_file).parent)
    return os.path.relpath(to_path, start if start != "." else ".").replace("\\", "/")


def _md_link(from_file: str, to_path: str, label: str | None = None) -> str:
    return f"[{label or to_path}]({link(from_file, to_path)})"


# ----------------------------------------------------------------------------- SKILL.md

def render_skill(s: SkillSpec, platform_dir: str, repo_paths: set[str]) -> str:
    me = f"{platform_dir}/skills/{s.name}/SKILL.md"

    def ref(p: str) -> str:
        return _md_link(me, p, f"`{p}`") if p in repo_paths else f"`{p}`"

    lines = [f"# Skill: {s.name}", "", f"**When to use:** {s.when_to_use}", ""]
    if s.scope:
        lines += ["## Scope", *[f"- `{x}`" for x in s.scope], ""]
    if s.prerequisites:
        lines += ["## Prerequisites", *[f"- {x}" for x in s.prerequisites], ""]
    lines += ["## Steps", *[f"{i}. {x}" for i, x in enumerate(s.steps, 1)], ""]
    if s.example:
        lines += ["## Existing example", f"- {ref(s.example)}", ""]
    if s.sources:
        lines += ["## Sources", *[f"- {ref(x)}" for x in s.sources], ""]
    lines += ["## Verification", *[f"- {x}" for x in (s.verification or ["No verification found in the repository."])], ""]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- HOOKS.md

def render_hooks(hooks: list[HookProposal], commands: list[CommandInfo]) -> str:
    lines = ["# Hooks and verification", "",
             "Status `active` means the hook exists in the repository. Status `proposed` means INIT suggests it. "
             "Enabling a proposed hook is an operator decision.", "",
             "| Hook | Status | Trigger | Command | Scope | Failure behavior | Evidence |",
             "|---|---|---|---|---|---|---|"]
    for h in hooks:
        ev = ", ".join(f"`{e}`" for e in h.evidence) or "-"
        lines.append(f"| {h.name} | {h.status} | {h.trigger} | `{h.command}` | {h.scope or '-'} | "
                     f"{h.failure_behavior or '-'} | {ev} |")
    if commands:
        lines += ["", "## Verification commands", "", "| Command | Purpose | Status | Source |", "|---|---|---|---|"]
        lines += [f"| `{c.command}` | {c.purpose} | {c.status} | `{c.source}` |" for c in commands]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- STE100 check

def ste_sentence_check(markdown: str, max_words: int = 25) -> list[str]:
    """Automated check of descriptive sentence length only (not a full STE100 review)."""
    text = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
    long: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "|", "<!--")):
            continue
        s = re.sub(r"^([-*]|\d+\.)\s+", "", s)
        s = re.sub(r"`[^`]*`", "X", s)
        s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
        for sentence in re.split(r"(?<=[.!?])\s+", s):
            words = sentence.split()
            if len(words) > max_words:
                long.append(f"{len(words)} words: {sentence[:90]}...")
    return long


# ----------------------------------------------------------------------------- INIT.md

def _tree(paths: list[str]) -> str:
    tree: dict = {}
    for p in sorted(paths):
        node = tree
        for part in PurePosixPath(p).parts:
            node = node.setdefault(part, {})
    out: list[str] = []

    def walk(node: dict, prefix: str) -> None:
        items = list(node.items())
        for i, (name, child) in enumerate(items):
            last = i == len(items) - 1
            out.append(f"{prefix}{'└── ' if last else '├── '}{name}{'/' if child else ''}")
            walk(child, prefix + ("    " if last else "│   "))

    walk(tree, "")
    return "\n".join(out)


def render_report(*, state: dict, writes: list[WriteResult], alerts: list[Alert], broken_links: list[str],
                  unknown_evidence: list[str], ste: dict, platform_dir: str, published: dict) -> str:
    me = f"{platform_dir}/init/INIT.md"
    inv = state["inventory"]
    req = state["request"]
    analysis = state.get("analysis") or {}
    plan = state.get("plan") or {}
    skills = (state.get("skills") or {}).get("skills", [])
    automation = state.get("automation") or {}
    planned = {f"{platform_dir}/{f['path']}": f for f in plan.get("files", [])}
    ok = [w for w in writes if w.action in ("created", "updated", "kept", "planned")]
    failed = [w for w in writes if w.action in ("failed", "skipped")]
    md_written = [w for w in writes if w.action in ("created", "updated") and w.path.endswith(".md")]

    L: list[str] = [
        "# INIT report",
        "",
        f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Run: `{state['run_id']}`" + (f" · feature `{req.get('feature_id')}`" if req.get("feature_id") else ""),
        f"- Repository: {inv['version'].get('remote') or state.get('repo_url') or inv['root']}",
        f"- Base commit: `{state.get('base_sha') or inv['version'].get('commit') or 'unknown'}`"
        f" (ref `{state.get('base_ref') or '-'}`) · branch `{state.get('branch') or inv['version'].get('branch') or '-'}`",
        "",
        "## Start here",
        "",
        "Requirements and Architect start with these files:",
        "",
    ]
    for name in ("AGENTS.md", "ARCHITECTURE.md", "RULES.md"):
        p = f"{platform_dir}/{name}"
        if any(w.path == p and w.action in ("created", "updated", "kept") for w in writes):
            L.append(f"- {_md_link(me, p, name)}: {planned.get(p, {}).get('purpose', '')}")
    if analysis.get("summary"):
        L += ["", f"> {analysis['summary']}"]

    L += ["", "## Scan scope", "",
          f"- Files scanned: {len(inv['files'])} · components: {len(inv['components'])}"
          f" · analyzed: {len(analysis.get('components', []))}",
          f"- Include: {req.get('inputs', {}).get('scan_scope', {}).get('include') or 'all'}"
          f" · exclude: {req.get('inputs', {}).get('scan_scope', {}).get('exclude') or 'none'}"]
    cov = state.get("coverage") or {}
    if cov.get("left_out"):
        L.append(f"- Files not sent to the model (context budget): {len(cov['left_out'])}")
    if cov.get("truncated"):
        L.append(f"- Files sent truncated: {len(cov['truncated'])}")
    if inv.get("skipped_large"):
        L.append(f"- Files skipped as too large: {', '.join(f'`{p}`' for p in inv['skipped_large'][:10])}")

    L += ["", "## Harness map", "", "| File | Scope | Purpose | Result |", "|---|---|---|---|"]
    for w in writes:
        meta = planned.get(w.path, {})
        shown = f"`{w.path}`" if w.action in ("failed", "skipped") else _md_link(me, w.path, w.path)
        L.append(f"| {shown} | {meta.get('scope', '-')} | {meta.get('purpose', '-')} | "
                 f"{w.action}{' (' + w.detail + ')' if w.detail else ''} |")

    L += ["", "## Files written", "",
          f"Markdown files created or updated: **{len(md_written)}** (this report is not counted).", "",
          "```text", _tree([w.path for w in ok]) or "(none)", "```"]

    L += ["", "## Skills", ""]
    local = [s for s in skills if s["kind"] == "local"]
    shared = [s for s in skills if s["kind"] == "shared_ref"]
    pending = [s for s in skills if s["kind"] == "needs_evidence"]
    for s in local:
        p = f"{platform_dir}/skills/{s['name']}/SKILL.md"
        L.append(f"- {_md_link(me, p, s['name'])} (local): {s['when_to_use']}")
    for s in shared:
        L.append(f"- `{s.get('shared_skill_id') or s['name']}` (shared, by reference): {s['when_to_use']}")
    for s in pending:
        L.append(f"- `{s['name']}` (needs evidence): missing {', '.join(s.get('missing_evidence') or ['-'])}")
    if not skills:
        L.append("- No skill had enough evidence.")

    hooks = automation.get("hooks", [])
    L += ["", "## Hooks", ""]
    if hooks:
        L += ["| Hook | Status | Trigger | Command | Failure behavior |", "|---|---|---|---|---|"]
        L += [f"| {h['name']} | {h['status']} | {h['trigger']} | `{h['command']}` | {h.get('failure_behavior') or '-'} |"
              for h in hooks]
    else:
        L.append("- No hooks found or proposed.")

    cmds = automation.get("verification_commands") or analysis.get("commands") or []
    if cmds:
        L += ["", "## Commands", "", "| Command | Status | Source |", "|---|---|---|"]
        L += [f"| `{c['command']}` | {c['status']} | `{c.get('source', '')}` |" for c in cmds]

    L += ["", "## ASD-STE100", "",
          f"- Architecture prose was written with ASD-STE100 Issue 9 descriptive writing rules in the prompt.",
          f"- Automated check: sentence length (max 25 words) on {', '.join(ste.get('files', [])) or 'no files'}: "
          f"{len(ste.get('long_sentences', []))} long sentence(s).",
          "- A separate standards conformance review was **not** performed."]
    for s in ste.get("long_sentences", [])[:10]:
        L.append(f"  - {s}")

    rag = [a for a in alerts if a.kind == "rag"]
    if rag:
        L += ["", "## RAG alert", ""]
        for a in rag:
            L += [f"- **Affected scope:** {', '.join(a.affected_scope) or '-'}",
                  f"- **Reason:** {a.reason}",
                  f"- **Missing for:** {', '.join(a.missing_for) or '-'}",
                  f"- **Index or provide:** {', '.join(a.suggested_material) or '-'}",
                  "- The run continued with the available evidence. Building an index is the operator's decision.", ""]

    issues = failed or plan.get("conflicts") or broken_links or unknown_evidence
    L += ["", "## Failures, conflicts and limits", ""]
    for w in failed:
        L.append(f"- Write {w.action}: `{w.path}`: {w.detail}")
    for c in plan.get("conflicts", []):
        L.append(f"- Conflict (needs a decision): {c}")
    for b in broken_links:
        L.append(f"- Broken link: {b}")
    for e in unknown_evidence[:20]:
        L.append(f"- Evidence path not found in the scanned copy: `{e}`")
    for g in (cov.get("gaps") or [])[:30]:
        L.append(f"- Coverage gap: {g}")
    for a in alerts:
        if a.kind not in ("rag", "write_failure", "conflict") or not a.affected_scope and a.kind == "write_failure":
            L.append(f"- {a.kind} ({a.severity}): {a.reason}")
    if not issues and not cov.get("gaps"):
        L.append("- None.")

    if plan.get("recommendations"):
        L += ["", "## Recommendations", "", *[f"- {r}" for r in plan["recommendations"]]]

    if analysis.get("project_context"):
        L += ["", "## Project context", "", "| Key | Value |", "|---|---|"]
        L += [f"| {k} | {v} |" for k, v in analysis["project_context"].items()]

    if published:
        L += ["", "## Publication", "", f"- Branch: `{published.get('branch')}` · pushed: {published.get('pushed')}"]
    return "\n".join(L) + "\n"

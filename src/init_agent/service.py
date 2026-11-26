"""Entry point used by the API and the CLI: run INIT once and build the result for the UI."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from .config import InitSettings, load_init_settings
from .graph import ProgressFn, build_init_graph
from .llm import GenericAgentModel
from .models import Alert, InitRequest, InitResult
from .prompt_pack import PromptPack


def _status(alerts: list[Alert], writes: list[dict], fatal: str | None) -> str:
    if fatal:
        return "failed"
    if any(a.severity in ("warning", "error") for a in alerts) or any(w["action"] == "failed" for w in writes):
        return "completed_with_warnings"
    return "completed"


async def arun_init(
    request: InitRequest | dict,
    *,
    llm: BaseChatModel | None = None,
    settings: InitSettings | None = None,
    run_id: str | None = None,
    on_progress: ProgressFn | None = None,
) -> InitResult:
    req = request if isinstance(request, InitRequest) else InitRequest.model_validate(request)
    settings = settings or load_init_settings()
    run_id = run_id or uuid.uuid4().hex[:12]
    thread_id = req.thread_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"init-agent/{run_id}"))
    llm = llm or GenericAgentModel(thread_id=thread_id)
    graph = build_init_graph(llm, settings, PromptPack.load(settings.prompts_override_dir), on_progress)

    fatal = None
    state: dict[str, Any] = {}
    try:
        state = await graph.ainvoke({"run_id": run_id, "request": req.model_dump(), "alerts": [], "progress": []})
    except Exception as exc:  # noqa: BLE001 - the UI gets a failed result, never a stack trace
        fatal = f"{type(exc).__name__}: {exc}"

    alerts = [Alert(**a) for a in state.get("alerts", [])]
    if fatal:
        alerts.append(Alert(kind="write_failure", severity="error", reason=fatal))
    writes = state.get("writes", [])
    published = state.get("published", {})
    analysis = state.get("analysis") or {}
    report = state.get("report_path")
    artifacts = ([report, settings.status_path] if report and not req.dry_run else []) + [
        w["path"] for w in writes if w["action"] in ("created", "updated")]
    created = sum(w["action"] == "created" for w in writes)
    updated = sum(w["action"] == "updated" for w in writes)

    return InitResult(
        fixture=req.fixture,
        feature_id=req.feature_id,
        run_id=run_id,
        status=_status(alerts, writes, fatal),
        artifacts=artifacts,
        output={
            "summary": (f"INIT {'failed: ' + fatal if fatal else 'finished'}. "
                        f"{created} files created, {updated} updated under {settings.platform_dir}/."
                        + (f" Branch `{published.get('branch')}` pushed." if published.get("pushed") else "")),
            "report_path": report,
            "report_file": str(Path(state["repo_root"]) / report) if report and state.get("repo_root") else None,
            "repository": {"url": state.get("repo_url"), "base_ref": state.get("base_ref"),
                           "base_commit": state.get("base_sha")},
            "branch": published.get("branch") or state.get("branch"),
            "commit": published.get("commit"),
            "pushed": bool(published.get("pushed")),
            "files": [{"path": w["path"], "action": w["action"], "detail": w["detail"]} for w in writes],
            "alerts": [a.model_dump() for a in alerts],
            "rag_alert": next((a.model_dump() for a in alerts if a.kind == "rag"), None),
            "project_context": analysis.get("project_context", {}),
            "prompt_pack": state.get("prompt_ids", {}),
            "progress": state.get("progress", []),
            "thread_id": thread_id,
        },
    )


def run_init(request: InitRequest | dict, **kwargs: Any) -> InitResult:
    return asyncio.run(arun_init(request, **kwargs))

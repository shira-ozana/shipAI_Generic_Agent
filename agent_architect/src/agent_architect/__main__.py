"""CLI: python -m agent_architect --username U --repo-url URL --feature-id ID"""

from __future__ import annotations

import argparse
import sys
import uuid

from generic_agent.config import load_settings
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_architect.config import load_architect_settings
from agent_architect.credentials import fetch_github_credentials
from agent_architect.graph import build_architect_graph, discard_clone
from agent_architect.llm import make_generic_llm


def _show_analysis(payload: dict) -> None:
    print("\n" + "=" * 72)
    print(f"Requirement analysis for feature {payload['feature_id']} ({payload['requirement_path']})")
    print("=" * 72)
    print(payload["summary"])
    for title, key in [
        ("Functional requirements", "functional_requirements"),
        ("Non-functional requirements", "non_functional_requirements"),
        ("Constraints", "constraints"),
        ("Affected areas", "affected_areas"),
        ("Assumptions", "assumptions"),
    ]:
        if payload.get(key):
            print(f"\n{title}:")
            for item in payload[key]:
                print(f"  - {item}")
    if payload.get("questions"):
        print("\nQuestions for you:")
        for i, question in enumerate(payload["questions"], 1):
            print(f"  {i}. {question}")
    print("=" * 72)


def _ask_answers() -> dict:
    print("Answer the questions and correct anything above (one line; Enter = accept the assumptions):")
    return {"answers": input("> ").strip()}


def _show_draft(payload: dict) -> None:
    print("\n" + "=" * 72)
    print(f"HLD draft for feature {payload['feature_id']} (revision {payload['revision']})")
    print("=" * 72)
    print(payload["overall_hld"])
    print("\nComponents:")
    for c in payload["components"]:
        print(f"  - {c['architecture_path']}: {c['name']}")
    print("=" * 72)


def _ask_decision() -> dict:
    answer = input("Approve and push? [y] approve / [n] reject / or type feedback: ").strip()
    if answer.lower() in {"y", "yes"}:
        return {"approved": True}
    if answer.lower() in {"", "n", "no"}:
        return {"approved": False}
    return {"approved": False, "feedback": answer}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_architect", description="SDLC Architect Agent")
    parser.add_argument("--username", required=True, help="Username registered in the SDLC backend")
    parser.add_argument("--repo-url", required=True, help="GitHub repository URL registered for the user")
    parser.add_argument("--feature-id", required=True, help="Feature ID (the one the Requirement Agent used)")
    parser.add_argument("--auto-approve", action="store_true", help="Skip the human review step")
    args = parser.parse_args(argv)

    settings = load_architect_settings()
    cursor_settings = load_settings()
    graph = build_architect_graph(
        llm=make_generic_llm(cursor_settings),
        credentials=lambda username, repo_url: fetch_github_credentials(settings, username, repo_url),
        model_name=cursor_settings.model,
        author_name=settings.git_author_name,
        author_email=settings.git_author_email,
        source_branch=settings.source_branch,
        target_branch=settings.target_branch,
        require_approval=not args.auto_approve,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    inputs: object = {"username": args.username, "repo_url": args.repo_url, "feature_id": args.feature_id}

    try:
        while True:
            graph.invoke(inputs, config)
            snapshot = graph.get_state(config)
            if not snapshot.interrupts:
                break
            payload = snapshot.interrupts[0].value
            if payload.get("stage") == "clarify":
                _show_analysis(payload)
                inputs = Command(resume=_ask_answers())
            else:
                _show_draft(payload)
                inputs = Command(resume=_ask_decision())
    except Exception as exc:  # noqa: BLE001
        discard_clone(graph.get_state(config).values)
        print(f"Architect Agent failed: {exc}", file=sys.stderr)
        return 1

    final = graph.get_state(config).values
    print(f"\nResult: {final.get('result')}")
    if final.get("commit_sha"):
        print(f"Commit: {final['commit_sha']}")
        for path in final.get("written_files", []):
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

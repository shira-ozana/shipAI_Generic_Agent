"""CLI: `python -m init_agent [--repo-path PATH] [--request request.json] [--dry-run]`.

Without --repo-path, INIT clones REPO_URL from .env, writes under platform/ on branch
shipAI, commits and pushes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import InitRequest
from .service import run_init


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m init_agent", description=__doc__)
    parser.add_argument("--request", help="JSON file with an InitRequest (for example 01-init/request.json)")
    parser.add_argument("--repo-path", help="use a local working copy instead of cloning REPO_URL")
    parser.add_argument("--repo-url", help="override REPO_URL from .env")
    parser.add_argument("--feature-id")
    parser.add_argument("--dry-run", action="store_true", help="plan and draft, write nothing")
    args = parser.parse_args(argv)

    data = json.loads(Path(args.request).read_text(encoding="utf-8")) if args.request else {}
    req = InitRequest.model_validate(data)
    # Fixture requests carry repo_path ".": ignore it unless --repo-path is given.
    req.project.repo_path = args.repo_path
    if args.repo_url:
        req.project.repo_url = args.repo_url
    if args.feature_id:
        req.feature_id = args.feature_id
    req.dry_run = args.dry_run or req.dry_run

    def show(node: str, msg: str) -> None:
        print(f"[{node}] {msg}", file=sys.stderr)

    result = run_init(req, on_progress=show)
    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

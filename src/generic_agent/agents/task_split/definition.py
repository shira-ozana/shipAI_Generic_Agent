"""Compatibility re-export: request building and limits moved to ``task_split_agent.planner``.

The ``AgentDefinition`` builder (``build_task_split_definition``) is gone: Task Split
now passes its prompt pack to ``generic_agent.runner.GenericAgent``.
"""

from task_split_agent.planner import (  # noqa: F401
    MAX_HLS_CHARS,
    MAX_HLS_DOCUMENTS,
    MAX_NOTES_CHARS,
    MAX_TITLE_CHARS,
    HlsDocument,
    build_planning_request,
)

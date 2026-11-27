"""Compatibility re-export: the Task Split agent moved to the ``task_split_agent`` package.

New code imports from ``task_split_agent``. These names stay importable from
``generic_agent.agents.task_split`` for existing callers.
"""

from task_split_agent import *  # noqa: F401,F403
from task_split_agent import __all__  # noqa: F401

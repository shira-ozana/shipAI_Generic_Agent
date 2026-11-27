"""Windows fix for cursor-sdk (checked on 1.5.2): bridge launch fails with WinError 10038.

`cursor_sdk._bridge._read_discovery` waits for the bridge's stderr with
`selectors.select()` on a pipe. On Windows `select()` accepts sockets only, so every
`Agent.prompt` fails before the first request. This module replaces that function, on
Windows only, with one that reads the pipe in a daemon thread. The contract is the same:
return the discovery payload; lines read after it stay in `lines` for the SDK's own
stderr thread. Remove this module when cursor-sdk fixes the bug.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from typing import Any


def install() -> None:
    if sys.platform != "win32":
        return
    try:
        from cursor_sdk import _bridge
    except ImportError:          # SDK layout changed: leave it alone
        return
    if getattr(_bridge._read_discovery, "_generic_agent_patch", False):
        return

    StderrLines = _bridge.StderrLines
    CursorSDKError = _bridge.CursorSDKError
    parse_discovery_line = _bridge.parse_discovery_line

    def _read_discovery(
        process: subprocess.Popen[str],
        timeout: float,
        lines: Any = None,
    ) -> Mapping[str, Any]:
        if process.stderr is None:
            raise CursorSDKError("Bridge process stderr is unavailable")
        buffered = lines if lines is not None else StderrLines()
        fd = process.stderr.fileno()
        seen: list[str] = []
        outcome: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def reader() -> None:
            # Stops right after the discovery line, so it never competes with the
            # SDK's stderr thread that starts after launch.
            try:
                while True:
                    chunk = os.read(fd, 8192)        # blocking read works on Windows pipes
                    if not chunk:
                        last = buffered.finish()
                        if last is not None:
                            seen.append(last)
                            found = parse_discovery_line(last)
                            if found is not None:
                                outcome.put(("ok", found))
                                return
                        outcome.put(("eof", None))
                        return
                    buffered.feed(chunk)
                    while (line := buffered.pop_line()) is not None:
                        seen.append(line)
                        found = parse_discovery_line(line)
                        if found is not None:
                            outcome.put(("ok", found))
                            return
            except BaseException as exc:  # noqa: BLE001 - reported to the caller
                outcome.put(("error", exc))

        threading.Thread(target=reader, name="cursor-bridge-discovery", daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                kind, value = outcome.get(timeout=0.1)
            except queue.Empty:
                continue
            if kind == "ok":
                return value
            if kind == "error":
                raise value
            exit_code = process.wait(timeout=5)
            raise CursorSDKError(f"Bridge exited before discovery with status {exit_code}: " + "".join(seen))
        raise CursorSDKError("Timed out waiting for bridge discovery")

    _read_discovery._generic_agent_patch = True  # type: ignore[attr-defined]
    _bridge._read_discovery = _read_discovery

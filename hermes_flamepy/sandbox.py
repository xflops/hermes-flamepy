"""Hermes tool that runs Python and shell snippets in a Flame sandbox."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Mapping

from .common import (
    DEFAULT_IMAGE,
    DEFAULT_LANGUAGE,
    MAX_GENERATIONS,
    RUN_ENVS,
    STALE_CODES,
    error_code,
    flame_id,
    language_property,
)

SANDBOX_SCHEMA: dict[str, Any] = {
    "name": "sandbox",
    "description": (
        "Execute Python or shell code in a remote sandbox without affecting the "
        "local system. Ideal for running untrusted code, testing scripts, or "
        "offloading computations. Calls share one sandbox and run one at a "
        "time, so files under /tmp persist between calls. The sandbox has "
        "network access: install packages with `pip install <pkg>` in a shell "
        "call and import them in later calls. Returns UTF-8 stdout; a nonzero "
        "exit code is reported as an error including stderr."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python or shell source code to execute. Only /tmp is writable.",
            },
            "language": language_property("Runtime for the code snippet."),
            "timeout": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Optional timeout in seconds for this run.",
            },
        },
        "required": ["code"],
        "additionalProperties": False,
    },
}


class SandboxToolError(Exception):
    """Expected tool-level error raised to Hermes."""


def check_sandbox_available() -> bool:
    """Return True when the Flame Python SDK exposes the agent Sandbox."""
    try:
        from flamepy.agent import Sandbox  # noqa: F401
    except Exception:
        return False
    return True


async def handle_sandbox(args: Mapping[str, Any] | None, **_kwargs: Any) -> str:
    """Async Hermes registry handler for the sandbox tool."""
    # Hermes also passes session_id/task_id; they do not select a sandbox.
    return await asyncio.to_thread(run_code, args or {})


def run_code(args: Mapping[str, Any], *, sandbox_cls: Any = None) -> str:
    """Run one snippet in the machine's sandbox and return its stdout."""
    if sandbox_cls is None:
        from flamepy.agent import Sandbox as sandbox_cls

    base_id = flame_id("sandbox")

    def run(sandbox: Any) -> Any:
        return sandbox.run_code(
            args.get("code"),
            language=args.get("language", DEFAULT_LANGUAGE),
            envs=RUN_ENVS,
            timeout=args.get("timeout"),
        )

    # Reuse the cached handle: connecting and fetching state cost ~700 ms per call.
    sandbox = _HANDLES.get(base_id)
    if sandbox is None:
        sandbox = _HANDLES[base_id] = _open_sandbox(sandbox_cls, base_id)
    try:
        result = run(sandbox)
    except Exception as exc:
        # Closed, suspended or deleted behind our back: reopen once and retry.
        if error_code(exc) not in STALE_CODES:
            raise
        sandbox = _HANDLES[base_id] = _open_sandbox(sandbox_cls, base_id)
        result = run(sandbox)

    stdout = result.stdout_text()
    if result.exit_code != 0:
        message = f"sandbox exited with code {result.exit_code}"
        if stderr := result.stderr_text():
            message += f"\nstderr:\n{stderr}"
        if stdout:
            message += f"\nstdout:\n{stdout}"
        raise SandboxToolError(message)
    return stdout


_OPEN_LOCK = threading.Lock()
_HANDLES: dict[str, Any] = {}


def _open_sandbox(sandbox_cls: Any, base_id: str) -> Any:
    """Reconnect to the session's sandbox, resume it, or create it.

    A closed sandbox keeps its id on the cluster, so later generations get a
    numeric suffix.
    """
    with _OPEN_LOCK:
        for generation in range(MAX_GENERATIONS):
            sandbox_id = base_id if generation == 0 else f"{base_id}-{generation}"
            try:
                sandbox = sandbox_cls.connect(sandbox_id)
            except Exception as exc:
                code = error_code(exc)
                if code == "NOT_FOUND":
                    return sandbox_cls.create(sandbox_id=sandbox_id, image=DEFAULT_IMAGE)
                if code == "INVALID_ARGUMENT":  # closed, or not a sandbox
                    continue
                raise
            state = sandbox.state
            if state == "suspended":
                sandbox.resume()
                return sandbox
            if state == "open":
                return sandbox
    raise SandboxToolError(f"could not open a sandbox for {base_id}")

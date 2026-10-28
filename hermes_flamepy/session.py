"""Hermes tool that runs batches of snippets in parallel in a Flame agent session."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Mapping

from .common import (
    DEFAULT_IMAGE,
    DEFAULT_LANGUAGE,
    LANGUAGES,
    MAX_GENERATIONS,
    RUN_ENVS,
    STALE_CODES,
    error_code,
    flame_id,
    language_property,
)

MAX_CODES = 64
FAILED_EXIT_CODE = -1  # the snippet could not run, so it has no exit code of its own

SESSION_SCHEMA: dict[str, Any] = {
    "name": "session",
    "description": (
        "Run many Python or shell snippets in parallel in a remote Flame "
        "session, without affecting the local system. Each snippet in codes "
        "runs in its own executor, so they execute concurrently and do not "
        "share files; use the sandbox tool "
        "when later steps need files from earlier ones. The snippets have "
        "network access and only /tmp is writable. The session is reused "
        "across calls. Returns a JSON array with one object per snippet, in "
        "order, each {\"exit_code\", \"stdout\", \"stderr\"}. An exit_code other than "
        "0 means that snippet failed; if it could not run at all, exit_code is "
        "-1 and stderr holds the error. One failing snippet does not affect "
        "the others."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CODES,
                "items": {"type": "string"},
                "description": "Source snippets to run in parallel, one executor each.",
            },
            "language": language_property("Runtime for every snippet in codes."),
            "timeout": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Optional timeout in seconds for each snippet.",
            },
        },
        "required": ["codes"],
        "additionalProperties": False,
    },
}


class SessionToolError(Exception):
    """Expected tool-level error raised to Hermes."""


def check_session_available() -> bool:
    """Return True when the Flame Python SDK exposes the agent Session."""
    try:
        from flamepy.agent import open_session  # noqa: F401
    except Exception:
        return False
    return True


async def handle_session(args: Mapping[str, Any] | None, **_kwargs: Any) -> str:
    """Async Hermes registry handler for the session tool."""
    # Hermes also passes session_id/task_id; they do not select a session.
    results = await asyncio.to_thread(run_codes, args or {})
    return json.dumps([_to_json(result) for result in results])


_LOCK = threading.Lock()
_HANDLES: dict[str, Any] = {}


def run_codes(args: Mapping[str, Any], *, open_session: Any = None) -> list[Any]:
    """Run a batch of snippets in the machine's session, in parallel.

    Returns one entry per snippet, in order: its `CommandResult`, or the
    exception that failed it, so one failure does not hide the others.
    """
    language = args.get("language", DEFAULT_LANGUAGE)
    codes = _validate_codes(args.get("codes"), language)
    if open_session is None:
        from flamepy.agent import open_session

    def submit_all(session: Any) -> list[Any]:
        return _submit_codes(session, codes, language=language, timeout=args.get("timeout"))

    session = _get_handle(open_session)
    try:
        futures = submit_all(session)
    except Exception as exc:
        # Closed or deleted behind our back: reopen once and retry.
        if error_code(exc) not in STALE_CODES:
            raise
        _drop_handle()
        futures = submit_all(_get_handle(open_session))

    return [_outcome(future) for future in futures]


def _outcome(future: Any) -> Any:
    try:
        return future.result()
    except Exception as exc:
        return exc


def _validate_codes(codes: Any, language: Any) -> list[str]:
    if not isinstance(codes, list) or not 1 <= len(codes) <= MAX_CODES:
        raise SessionToolError(f"codes must be a list of 1-{MAX_CODES} strings")
    if not all(isinstance(code, str) for code in codes):
        raise SessionToolError("codes must contain only strings")
    if language not in LANGUAGES:
        raise SessionToolError('language must be "python" or "shell"')
    return codes


def _submit_codes(session: Any, codes: list[str], *, language: str, timeout: float | None = None) -> list[Any]:
    """Submit every snippet to the session at once; one future of a result each."""
    return [
        session.submit_code(code, language=language, envs=RUN_ENVS, timeout=timeout)
        for code in codes
    ]


def _get_handle(open_session: Any) -> Any:
    key = flame_id("session")
    with _LOCK:
        session = _HANDLES.get(key)
        if session is None:
            session = _HANDLES[key] = _open_session(open_session)
        return session


def _open_session(open_session: Any) -> Any:
    """Open this machine's session, creating it on first use.

    A closed session keeps its id on the cluster, so later generations get a
    numeric suffix.
    """
    base_id = flame_id("session")
    for generation in range(MAX_GENERATIONS):
        ssn_id = base_id if generation == 0 else f"{base_id}-{generation}"
        try:
            return open_session(ssn_id=ssn_id, image=DEFAULT_IMAGE)
        except Exception as exc:
            if error_code(exc) != "INVALID_ARGUMENT":  # closed, or not an agent session
                raise
    raise SessionToolError("could not open a session")


def _drop_handle() -> None:
    with _LOCK:
        session = _HANDLES.pop(flame_id("session"), None)
    if session is not None:
        try:
            session.close()
        except Exception:
            pass


def _to_json(result: Any) -> dict[str, Any]:
    if isinstance(result, Exception):
        return {"exit_code": FAILED_EXIT_CODE, "stdout": "", "stderr": str(result)}
    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout_text(),
        "stderr": result.stderr_text(),
    }

"""Tool handler for running snippets through Flame's flmexec application."""

from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Mapping

DEFAULT_APPLICATION = "flmexec"
TOOL_NAME = "flmexec"

FLMEXEC_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Run Python or shell code in Flame's flmexec application. The tool uses "
        "the active Hermes session id as the Flame session id, so repeated calls "
        "from the same Hermes session reuse the same Flame session. For Python "
        "code with third-party dependencies, include uv inline script metadata "
        "at the top of the snippet. Returns only UTF-8 stdout and raises on "
        "errors."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["python", "shell"],
                "description": "Runtime for the code snippet.",
            },
            "code": {
                "type": "string",
                "description": (
                    "UTF-8 Python or shell source code to execute. For "
                    "language=python, flmexec runs the snippet with uv run, so "
                    "dependencies should be declared with PEP 723 uv script "
                    "metadata."
                ),
            },
        },
        "required": ["language", "code"],
        "additionalProperties": False,
    },
}


class FlmexecToolError(Exception):
    """Expected tool-level error raised to Hermes."""


def check_flmexec_available() -> bool:
    """Return True when the Flame Python SDK is importable."""
    try:
        from flamepy.core.client import open_session  # noqa: F401
        from flamepy.core.types import SessionAttributes  # noqa: F401
    except Exception:
        return False
    return True


def handle_flmexec(args: Mapping[str, Any] | None, **kwargs: Any) -> str:
    """Hermes registry handler for flmexec."""
    return run_flmexec(args or {}, **kwargs)


def run_flmexec(
    args: Mapping[str, Any],
    *,
    open_session_fn: Callable[..., Any] | None = None,
    session_attrs_cls: type | None = None,
    **kwargs: Any,
) -> str:
    """Run one flmexec task and return its UTF-8 stdout."""
    language = _normalize_language(args.get("language"))
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        raise FlmexecToolError("code must be a non-empty string")
    _validate_utf8_text("code", code)

    hermes_session_id = _resolve_hermes_session_id(args, kwargs)

    if open_session_fn is None or session_attrs_cls is None:
        open_session_fn, session_attrs_cls = _load_flame_open_session()

    session = _open_or_create_flmexec_session(
        hermes_session_id,
        open_session_fn=open_session_fn,
        session_attrs_cls=session_attrs_cls,
    )

    request_payload = _encode_script_request(language, code)
    raw_output = session.invoke(request_payload)
    return _decode_script_output(raw_output)


def _load_flame_open_session() -> tuple[Callable[..., Any], type]:
    try:
        from flamepy.core.client import open_session
        from flamepy.core.types import SessionAttributes
    except Exception as exc:
        raise FlmexecToolError(
            "flamepy is required for flmexec; install and configure the Flame Python SDK"
        ) from exc
    return open_session, SessionAttributes


def _open_or_create_flmexec_session(
    session_id: str,
    *,
    open_session_fn: Callable[..., Any],
    session_attrs_cls: type,
) -> Any:
    attrs = _build_session_attrs(session_attrs_cls, session_id)
    try:
        return open_session_fn(session_id, attrs)
    except TypeError:
        # Older SDKs may name the second argument spec but still accept positionals.
        return open_session_fn(session_id=session_id, spec=attrs)


def _build_session_attrs(session_attrs_cls: type, session_id: str) -> Any:
    signature = inspect.signature(session_attrs_cls)
    params = set(signature.parameters)
    accepts_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    )
    attrs_kwargs: dict[str, Any] = {
        "application": DEFAULT_APPLICATION,
    }
    if accepts_kwargs or "id" in params:
        attrs_kwargs["id"] = session_id
    return session_attrs_cls(**attrs_kwargs)


def _normalize_language(value: Any) -> str:
    if not isinstance(value, str):
        raise FlmexecToolError("language must be 'python' or 'shell'")
    language = value.strip().lower()
    if language not in {"python", "shell"}:
        raise FlmexecToolError("language must be 'python' or 'shell'")
    return language


def _resolve_hermes_session_id(args: Mapping[str, Any], kwargs: Mapping[str, Any]) -> str:
    for key in ("session_id", "hermes_session_id"):
        value = kwargs.get(key) or args.get(f"_{key}")
        if value:
            return _validate_session_id(str(value))

    parent_agent = kwargs.get("parent_agent")
    value = getattr(parent_agent, "session_id", None)
    if value:
        return _validate_session_id(str(value))

    value = _session_id_from_stack()
    if value:
        return _validate_session_id(value)

    raise FlmexecToolError(
        "Hermes session_id was not available to flmexec; call this tool from an active Hermes session"
    )


def _session_id_from_stack() -> str | None:
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        depth = 0
        while frame is not None and depth < 50:
            for local_name in ("agent", "parent_agent", "self"):
                candidate = frame.f_locals.get(local_name)
                value = getattr(candidate, "session_id", None)
                if isinstance(value, str) and value:
                    return value
            frame = frame.f_back
            depth += 1
    finally:
        del frame
    return None


def _validate_session_id(value: str) -> str:
    session_id = value.strip()
    if not session_id:
        raise FlmexecToolError("Hermes session_id is empty")
    if any(ch in session_id for ch in "\r\n\x00"):
        raise FlmexecToolError("Hermes session_id contains invalid control characters")
    return session_id


def _validate_utf8_text(name: str, value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FlmexecToolError(f"{name} must be valid UTF-8 text") from exc


def _encode_script_request(language: str, code: str) -> bytes:
    payload = {
        "language": language,
        "code": code,
        "input": None,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _decode_script_output(raw_output: Any) -> str:
    if raw_output is None:
        return ""
    if isinstance(raw_output, str):
        _validate_utf8_text("flmexec output", raw_output)
        raw_bytes = raw_output.encode("utf-8")
    elif isinstance(raw_output, bytes):
        raw_bytes = raw_output
    elif isinstance(raw_output, bytearray):
        raw_bytes = bytes(raw_output)
    else:
        raise FlmexecToolError(f"flmexec returned unsupported output type {type(raw_output).__name__}")

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FlmexecToolError("flmexec output must be valid UTF-8") from exc

    try:
        decoded = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text

    if isinstance(decoded, dict) and "data" in decoded:
        data = decoded["data"]
        if data is None:
            return ""
        if isinstance(data, list):
            try:
                return bytes(data).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FlmexecToolError("flmexec output must be valid UTF-8") from exc
            except ValueError as exc:
                raise FlmexecToolError("flmexec output data contains non-byte values") from exc
        if isinstance(data, str):
            _validate_utf8_text("flmexec output", data)
            return data
        raise FlmexecToolError("flmexec output data must be UTF-8 bytes or text")
    return raw_text

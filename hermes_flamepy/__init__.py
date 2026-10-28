"""Hermes Flame Python SDK plugin."""

from __future__ import annotations

from .sandbox import SANDBOX_SCHEMA, check_sandbox_available, handle_sandbox
from .session import SESSION_SCHEMA, check_session_available, handle_session


def register(ctx) -> None:
    """Register Flame Python SDK backed tools with Hermes."""
    ctx.register_tool(
        name="sandbox",
        toolset="flamepy",
        schema=SANDBOX_SCHEMA,
        handler=handle_sandbox,
        check_fn=check_sandbox_available,
        is_async=True,
        description=SANDBOX_SCHEMA["description"],
    )
    ctx.register_tool(
        name="session",
        toolset="flamepy",
        schema=SESSION_SCHEMA,
        handler=handle_session,
        check_fn=check_session_available,
        is_async=True,
        description=SESSION_SCHEMA["description"],
    )


__all__ = ["register"]

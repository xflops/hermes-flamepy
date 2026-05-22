"""Hermes Flame Python SDK plugin."""

from __future__ import annotations

from .flmexec_tool import FLMEXEC_SCHEMA, check_flmexec_available, handle_flmexec


def register(ctx) -> None:
    """Register Flame Python SDK backed tools with Hermes."""
    ctx.register_tool(
        name="flmexec",
        toolset="hermes-flamepy",
        schema=FLMEXEC_SCHEMA,
        handler=handle_flmexec,
        check_fn=check_flmexec_available,
        description=FLMEXEC_SCHEMA["description"],
    )


__all__ = ["register"]

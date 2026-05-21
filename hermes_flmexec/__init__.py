"""Hermes flmexec plugin."""

from __future__ import annotations

from .flmexec_tool import FLMEXEC_SCHEMA, check_flmexec_available, handle_flmexec


def register(ctx) -> None:
    """Register the flmexec tool with Hermes."""
    ctx.register_tool(
        name="flmexec",
        toolset="flmexec",
        schema=FLMEXEC_SCHEMA,
        handler=handle_flmexec,
        check_fn=check_flmexec_available,
        description=FLMEXEC_SCHEMA["description"],
    )


__all__ = ["register"]

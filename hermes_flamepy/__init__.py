"""Flame terminal environment provider for Hermes."""

from __future__ import annotations


def register(ctx) -> None:
    """Register one backend; terminal and file tools belong to Hermes itself."""
    if not hasattr(ctx, "register_terminal_environment_provider"):
        raise RuntimeError("hermes-flamepy requires Hermes's terminal environment provider API; update Hermes first")
    from .provider import FlameProvider

    ctx.register_terminal_environment_provider(FlameProvider(ctx))


__all__ = ["register"]

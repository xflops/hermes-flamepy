"""Hermes directory-plugin entry point."""

try:
    from .hermes_flmexec import register
except ImportError:
    from hermes_flmexec import register

__all__ = ["register"]

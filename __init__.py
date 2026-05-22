"""Hermes directory-plugin entry point."""

try:
    from .hermes_flamepy import register
except ImportError:
    from hermes_flamepy import register

__all__ = ["register"]

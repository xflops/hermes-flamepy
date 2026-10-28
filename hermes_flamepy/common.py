"""Definitions shared by the sandbox and session tools."""

from __future__ import annotations

import functools
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

DEFAULT_IMAGE = "python:3.12-slim"
MAX_GENERATIONS = 20
LANGUAGES = ("python", "shell")
DEFAULT_LANGUAGE = "python"

# The sandbox root filesystem is read-only but /tmp is writable, so point pip at
# /tmp: a plain `pip install pkg` then works and is importable in later calls.
RUN_ENVS = {
    "PIP_TARGET": "/tmp/site-packages",
    "PYTHONPATH": "/tmp/site-packages",
}

# Flame errors that mean a cached handle is closed, suspended or deleted.
STALE_CODES = {"INVALID_STATE", "INVALID_ARGUMENT", "NOT_FOUND"}


MAC_FORMAT = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")
NO_MAC = "00:00:00:00:00:00"


@functools.cache
def machine_id() -> str:
    """12 hex digits identifying this machine: the MAC address of its first physical interface.

    Virtual interfaces (loopback, bridges, VPNs, containers) are skipped, since
    they come and go. `uuid.getnode()` cannot tell them apart, and invents a
    different random number in every process when it finds no MAC at all.
    """
    for mac in _physical_macs():
        mac = mac.strip().lower()
        if MAC_FORMAT.fullmatch(mac) and mac != NO_MAC:
            return mac.replace(":", "")
    raise RuntimeError("no physical network interface with a MAC address was found")


def _physical_macs() -> Iterator[str]:
    """MAC addresses of the physical interfaces, in a stable order."""
    sys_net = Path("/sys/class/net")
    if sys_net.is_dir():  # Linux: only physical devices have a `device` link
        for iface in sorted(sys_net.iterdir()):
            if (iface / "device").exists():
                try:
                    yield (iface / "address").read_text(encoding="ascii")
                except OSError:
                    continue
    elif sys.platform == "darwin":  # lists hardware ports only, in service order
        try:
            out = subprocess.run(
                ["/usr/sbin/networksetup", "-listallhardwareports"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return
        for line in out.splitlines():
            if line.startswith("Ethernet Address:"):
                yield line.split(":", 1)[1]
    else:  # no way to tell physical from virtual: trust getnode() unless it made one up
        node = uuid.getnode()
        if not (node >> 40) & 1:
            yield ":".join(f"{node:012x}"[i : i + 2] for i in range(0, 12, 2))


def flame_id(kind: str) -> str:
    """Flame id of this machine's sandbox or session, stable across Hermes restarts."""
    return f"hermes-{kind}-{machine_id()}"


def language_property(description: str) -> dict[str, Any]:
    """Schema of the `language` argument shared by the sandbox and session tools."""
    return {
        "type": "string",
        "enum": list(LANGUAGES),
        "default": DEFAULT_LANGUAGE,
        "description": description,
    }


def error_code(exc: Exception) -> str | None:
    """Name of the Flame error code carried by `exc`, if any."""
    return getattr(getattr(exc, "code", None), "name", None)

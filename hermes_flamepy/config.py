"""Native sandbox paths and controller working-directory defaults."""

import os

DEFAULT_IMAGE = "python:3.12-slim"
SANDBOX_HOME = "/root"
DEFAULT_CWD = "/workspace"

RUN_ENVS = {
    "HOME": SANDBOX_HOME,
    "TMPDIR": "/tmp",
    "XDG_CACHE_HOME": f"{SANDBOX_HOME}/.cache",
}


def controller_cwd():
    """Use Hermes's process cwd, with a fallback for a deleted host directory."""
    try:
        return os.getcwd()
    except OSError:
        return DEFAULT_CWD

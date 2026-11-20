"""Hermes terminal-backend discovery and profile-scoped configuration."""

from __future__ import annotations

from agent.terminal_env_provider import TerminalEnvironmentProvider

from tools.terminal_scope import terminal_env

from .config import DEFAULT_IMAGE, SANDBOX_HOME, controller_cwd


class FlameProvider(TerminalEnvironmentProvider):
    """Select with terminal.backend: flamepy."""

    name = "flamepy"
    display_name = "Flame"
    is_remote = True
    is_container = True
    supports_streams = False
    serves_hosted_scopes = False
    session_isolated_when_nonpersistent = True

    def __init__(self, ctx):
        self._ctx = ctx

    @property
    def description(self):
        """Label for Hermes's backend picker."""
        return "Run terminal commands and file tools in a persistent Flame sandbox."

    @property
    def default_cwd(self):
        """Honor scoped Hermes paths, including host-shaped absolute paths."""
        cwd = terminal_env("TERMINAL_CWD", "").strip()
        if cwd.startswith(("/", "~/")) or cwd == "~":
            return cwd
        return controller_cwd()

    @property
    def cache_path_base(self):
        """Translate Hermes cache references to the sandbox home."""
        return f"{SANDBOX_HOME}/.hermes"

    @property
    def skip_container_guards(self):
        """Keep normal command approvals for persistent workspaces."""
        # A persistent workspace is not disposable, even though it is remote.
        return False

    @property
    def strip_env_keys(self):
        """Keep controller TLS configuration out of model-authored processes."""
        return frozenset({"FLAME_CA_FILE", "FLAME_CERT_FILE", "FLAME_KEY_FILE"})

    def is_available(self):
        """Check the SDK API locally, without opening a cluster connection."""
        try:
            from flamepy.agent import Sandbox
        except ImportError:
            return False
        return callable(getattr(Sandbox, "submit_code", None))

    def setup_instructions(self):
        """Explain external prerequisites without installing them implicitly."""
        return [
            "Install flamepy>=0.7.0 in the Hermes Python environment and configure ~/.flame/flame.yaml.",
            "The cluster needs flmbox and runsc; the sandbox image needs bash and python3.",
            "Use plugins.isolation: in_process; terminal.cwd selects a directory created inside the sandbox.",
            "The cluster must include Flame #724 (writable sandbox root filesystem).",
        ]

    def create_environment(self, *, cwd, timeout, task_id="default", image=None,
                           container_config=None, **_kwargs):
        """Create or reconnect using the identity chosen by Hermes."""
        from .environment import FlameEnvironment

        config = container_config or {}
        return FlameEnvironment(
            cwd=cwd,
            timeout=timeout,
            task_id=task_id,
            image=image or self._ctx.get_config("image", DEFAULT_IMAGE),
            state_dir=self._ctx.state.data_dir,
            persistent=config.get("container_persistent", True),
        )

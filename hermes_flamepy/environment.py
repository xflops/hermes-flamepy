"""Hermes shell state and file operations over Flame's persistent sandbox."""

from __future__ import annotations

import hashlib
import shlex
import threading

from tools.environments.base import BaseEnvironment, EnvironmentConnectionError

from .config import RUN_ENVS, SANDBOX_HOME, controller_cwd
from .process import FlameProcess
from .store import _error_code, open_sandbox, suspend_sandbox


class FlameEnvironment(BaseEnvironment):  # pylint: disable=too-many-instance-attributes
    """One live Flame sandbox per Hermes identity, shared by its tool calls."""

    _stdin_mode = "payload"
    _snapshot_timeout = 120
    uses_host_filesystem = False
    cleanup_stops_processes = False

    def __init__(self, *, cwd, timeout, task_id, image, state_dir,  # pylint: disable=too-many-arguments
                 persistent=True, sandbox_cls=None):
        self.cwd = cwd or controller_cwd()
        if self.cwd == "~" or self.cwd.startswith("~/"):
            self.cwd = SANDBOX_HOME + self.cwd[1:]
        if not self.cwd.startswith("/") or "\x00" in self.cwd:
            raise ValueError("Flame working directory must be an absolute POSIX path or ~/path")
        super().__init__(cwd=self.cwd, timeout=timeout, env=dict(RUN_ENVS))
        self._persistent = persistent
        self.cleanup_stops_processes = not persistent
        self._close_lock = threading.Lock()
        self._closed = False
        self._termination_requested = False
        if sandbox_cls is None:
            from flamepy.agent import Sandbox as sandbox_cls
        self._sandbox_cls = sandbox_cls
        self._sandbox, recreated = open_sandbox(
            sandbox_cls, state_dir=state_dir, task_id=task_id, image=image, persistent=persistent,
        )
        # Keep Hermes's snapshot and cwd files stable when a new Python object
        # reconnects. Markers for each individual call remain Hermes-owned.
        shell_key = hashlib.sha256(self._sandbox.id.encode()).hexdigest()[:24]
        self._snapshot_path = f"{SANDBOX_HOME}/shell-{shell_key}.sh"
        self._cwd_file = f"{SANDBOX_HOME}/shell-{shell_key}.cwd"
        self._recreated_notice_pending = recreated
        try:
            self._initialize()
        except Exception:
            # Cancellation may already have requested suspension. Otherwise do
            # not discard a reused persistent workspace on a connection error.
            if not persistent:
                self._terminate()
            raise

    @property
    def sandbox_id(self):
        """The actual Flame ID, useful for operator inspection and cleanup."""
        return self._sandbox.id

    def _initialize(self):
        bootstrap = (
            f"mkdir -p -- {shlex.quote(SANDBOX_HOME)} {shlex.quote(self.cwd)} "
            f"{shlex.quote(RUN_ENVS['XDG_CACHE_HOME'])} || exit\n"
            f"builtin cd -- {self._quote_cwd_for_cd(self.cwd)} || exit\n"
            "command -v python3 >/dev/null || exit 127\n"
            f"if [ -f {shlex.quote(self._snapshot_path)} ]; then\n"
            f"  if [ -f {shlex.quote(self._cwd_file)} ]; then cat {shlex.quote(self._cwd_file)}; fi\n"
            "else exit 3; fi"
        )
        result = self._wait_for_process(self._run_bash(bootstrap, timeout=self._snapshot_timeout),
                                        timeout=self._snapshot_timeout)
        if result["returncode"] == 0:
            self._snapshot_ready = True
            self.cwd = result["output"].strip() or self.cwd
        elif result["returncode"] == 3:
            self.init_session()
            if not self._snapshot_ready:
                raise RuntimeError("Failed to initialize the Flame terminal shell snapshot")
        else:
            raise RuntimeError(f"Flame terminal bootstrap failed: {result['output']}")

    def _wrap_command(self, command, cwd, **kwargs):
        wrapped = super()._wrap_command(command, cwd, **kwargs)
        if cwd == self.cwd:
            # Keep restart cwd inside the sandbox, alongside Hermes's snapshot.
            # One-off file operations (e.g. polling from /) must not change it.
            save_cwd = f"pwd -P > {shlex.quote(self._cwd_file)}"
            wrapped = f"trap {shlex.quote(save_cwd)} EXIT\n{wrapped}"
        return wrapped

    def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
        if self._closed or self._termination_requested:
            raise EnvironmentConnectionError("Flame environment was terminated or suspended; reconnect to continue")
        # Login profiles may reset HOME. Restore sandbox-only paths after they
        # run, and merge stderr inside the remote shell to preserve ordering.
        exports = "\n".join(f"export {key}={shlex.quote(value)}" for key, value in RUN_ENVS.items())
        script = f"{exports}\n{cmd_string}"
        flag = "-lc" if login else "-c"
        code = f"exec bash {flag} {shlex.quote(script)} 2>&1"

        def submit():
            try:
                return self._sandbox.submit_code(
                    code, language="shell", envs=dict(RUN_ENVS),
                    input=None if stdin_data is None else stdin_data.encode("utf-8", errors="surrogateescape"),
                    timeout=timeout,
                )
            except Exception as exc:
                raise EnvironmentConnectionError(f"Cannot submit command to Flame: {exc}") from exc

        return FlameProcess(submit, lambda: self._terminate(wait=False), error_type=EnvironmentConnectionError)

    def _terminate(self, *, wait=True):
        with self._close_lock:
            if not self._closed:
                self._termination_requested = True
                # Use a fresh SDK handle so failed lifecycle RPCs can be retried.
                try:
                    handle = self._sandbox_cls.connect(self.sandbox_id)
                except Exception as exc:
                    if _error_code(exc) not in {"NOT_FOUND", "INVALID_ARGUMENT"}:
                        raise
                else:
                    if self._persistent:
                        suspend_sandbox(handle, wait=wait)
                    else:
                        handle.close()
                self._closed = True
                self.cleanup_stops_processes = not self._persistent

    def cleanup(self):
        """Suspend persistent sessions for reuse; close disposable sessions."""
        self._terminate()

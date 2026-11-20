"""Adapt Flame's result futures to Hermes's process-handle contract."""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable
from typing import Any


class FlameProcess:  # pylint: disable=too-many-instance-attributes
    """Expose a readable pipe while a daemon waits for a Flame command result.

    Flame returns complete results, so output becomes readable on completion.
    Pipe writes are buffered and may block until Hermes drains them; completion
    is published only after all output has been written.
    """

    def __init__(self, submit: Callable[[], Any], cancel: Callable[[], None], error_type=RuntimeError):
        self._cancel = cancel
        self._cancelled = threading.Event()
        self._done = threading.Event()
        self._error: Exception | None = None
        self._error_type = error_type
        self._returncode: int | None = None
        read_fd, write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
        self._thread = threading.Thread(target=self._run, args=(submit, write_fd), daemon=True)
        self._thread.start()

    def _run(self, submit, write_fd):
        try:
            with os.fdopen(write_fd, "wb") as output:
                future = submit()
                while not future.done():
                    if self._cancelled.wait(0.05):
                        return
                if self._cancelled.is_set():
                    return
                result = future.result()
                output.write(result.stdout)
                output.write(result.stderr)
                self._returncode = result.exit_code
        except Exception as exc:
            self._error = exc
        finally:
            if self._cancelled.is_set():
                self._returncode = -9
            self._done.set()

    @property
    def returncode(self):
        """The exit status, once all output is available."""
        return self._returncode if self._done.is_set() else None

    def poll(self):
        """Infrastructure errors remain errors, rather than silent command exits."""
        if not self._done.is_set():
            return None
        if self._error is not None and not self._cancelled.is_set():
            raise self._error_type(f"Flame command failed: {self._error}") from self._error
        return self._returncode

    def wait(self, timeout=None):
        """Wait for completion, with subprocess-compatible timeout behavior."""
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("Flame command", timeout)
        return self.poll()

    def kill(self):
        """Release the sandbox through its lifecycle policy before detaching.

        Persistent sessions suspend, draining remote work before release;
        disposable sessions close. Cancelling a future alone does neither.
        """
        if self._done.is_set() or self._cancelled.is_set():
            return
        self._cancel()
        # A failed remote close must not make a still-running command look dead.
        self._cancelled.set()

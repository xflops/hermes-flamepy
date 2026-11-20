"""Cancellation and error delivery through the SDK process adapter."""

from concurrent.futures import Future
import subprocess

import pytest

from hermes_flamepy.process import FlameProcess


def test_cancel_does_not_wait_for_remote_future():
    cancelled = []
    proc = FlameProcess(Future, lambda: cancelled.append(True))
    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(0.01)
    proc.kill()
    proc.kill()
    assert proc.wait(1) == -9
    assert proc.stdout.read() == ""
    proc.stdout.close()
    assert cancelled == [True]


def test_remote_error_is_visible():
    future = Future()
    future.set_exception(ConnectionError("worker unavailable"))
    proc = FlameProcess(lambda: future, lambda: None)
    with pytest.raises(RuntimeError, match="worker unavailable"):
        proc.wait(1)
    proc.stdout.close()

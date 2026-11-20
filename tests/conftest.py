"""Real Hermes shell behavior with a local stand-in for the Flame transport."""

import importlib.util
import os
import signal
import shutil
import subprocess
import sys
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

if os.getenv("HERMES_AGENT_PATH"):
    sys.path.insert(0, os.environ["HERMES_AGENT_PATH"])
if importlib.util.find_spec("agent") is None:
    raise pytest.UsageError("Set HERMES_AGENT_PATH to a Hermes checkout with the terminal provider API")


class FlameError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = SimpleNamespace(name=code)


class LocalSandbox:
    """Fake only SDK transport; run the actual shell scripts in subprocesses."""

    handles = {}
    created = []

    def __init__(self, sandbox_id):
        self.id = sandbox_id
        self.state = "open"
        self.calls = []
        self.processes = []
        self.lock = threading.Lock()
        self.close_calls = 0
        self.resume_calls = 0
        self.suspend_calls = 0

    @classmethod
    def create(cls, *, sandbox_id, image):
        if sandbox_id in cls.handles:
            raise FlameError("ALREADY_EXISTS")
        handle = cls.handles[sandbox_id] = cls(sandbox_id)
        cls.created.append((sandbox_id, image))
        return handle

    @classmethod
    def connect(cls, sandbox_id):
        if sandbox_id not in cls.handles:
            raise FlameError("NOT_FOUND")
        handle = cls.handles[sandbox_id]
        if handle.state != "open":
            raise FlameError("INVALID_ARGUMENT")
        return handle

    def resume(self):
        self.state = "open"
        self.resume_calls += 1

    def suspend(self):
        self.suspend_calls += 1
        self.state = "suspended"
        # Model the current Flame release's loss of sandbox-local files.
        shutil.rmtree(self.remote_home, ignore_errors=True)

    def submit_code(self, code, **kwargs):
        self.calls.append((code, kwargs))
        future = Future()

        def run():
            try:
                with self.lock:
                    if self.state != "open":
                        raise FlameError("INVALID_STATE")
                    proc = subprocess.Popen(
                        ["/bin/sh", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, start_new_session=True, cwd="/",
                        env={"PATH": os.environ["PATH"], **kwargs["envs"]},
                    )
                    self.processes.append(proc)
                try:
                    out, err = proc.communicate(kwargs.get("input"), timeout=kwargs.get("timeout"))
                    status = proc.returncode
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    out, err = proc.communicate()
                    status = 124
                future.set_result(SimpleNamespace(stdout=out, stderr=err, exit_code=status))
            except Exception as exc:
                future.set_exception(exc)

        threading.Thread(target=run, daemon=True).start()
        return future

    def close(self):
        with self.lock:
            self.close_calls += 1
            self.state = "closed"
            for proc in self.processes:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "controller"))


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    from hermes_flamepy import environment

    home = str(tmp_path / "remote")
    monkeypatch.setattr(environment, "SANDBOX_HOME", home)
    monkeypatch.setattr(environment, "RUN_ENVS", {
        key: value.replace("/root", home) for key, value in environment.RUN_ENVS.items()
    })
    LocalSandbox.handles = {}
    LocalSandbox.created = []
    LocalSandbox.remote_home = home
    import flamepy.core
    from flamepy.core.types import SessionState

    def get_session(sid):
        if sid not in LocalSandbox.handles:
            raise FlameError("NOT_FOUND")
        handle = LocalSandbox.handles[sid]
        return SimpleNamespace(id=sid, application="flmbox", state=SessionState[handle.state.upper()])

    monkeypatch.setattr(flamepy.core, "get_session", get_session)
    monkeypatch.setattr(flamepy.core, "suspend_session", lambda sid: LocalSandbox.handles[sid].suspend())
    monkeypatch.setattr(flamepy.core, "resume_session", lambda sid: LocalSandbox.handles[sid].resume())
    yield LocalSandbox
    for sandbox in LocalSandbox.handles.values():
        sandbox.close()


@pytest.fixture
def make_env(tmp_path, sdk):
    from hermes_flamepy.environment import FlameEnvironment

    def create(**kwargs):
        return FlameEnvironment(**{
            "cwd": str(tmp_path / "remote/workspace"), "timeout": 5, "task_id": "hermes-task",
            "image": "python:3.12-slim", "state_dir": tmp_path / "plugin-data", "sandbox_cls": sdk,
            **kwargs,
        })

    return create

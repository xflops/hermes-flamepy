import asyncio
from dataclasses import dataclass

import pytest

from hermes_flamepy import common, sandbox
from hermes_flamepy.sandbox import (
    SandboxToolError,
    _open_sandbox,
    handle_sandbox,
    run_code,
)


SID = common.flame_id("sandbox")


@pytest.fixture(autouse=True)
def _clear_handles():
    sandbox._HANDLES.clear()


@dataclass
class FakeResult:
    exit_code: int = 0
    stdout: bytes = b""
    stderr: bytes = b""

    def stdout_text(self):
        return self.stdout.decode()

    def stderr_text(self):
        return self.stderr.decode()


class FakeError(Exception):
    def __init__(self, name):
        super().__init__(name)
        self.code = type("Code", (), {"name": name})


class FakeBox:
    def __init__(self, state="open", result=None):
        self.state = state
        self.result = result or FakeResult()
        self.calls = []
        self.resumed = False

    def run_code(self, code, **kwargs):
        self.calls.append((code, kwargs))
        return self.result

    def resume(self):
        self.resumed = True
        self.state = "open"


class FakeSandbox:
    """Stands in for flamepy.agent.Sandbox; a None entry is a closed sandbox."""

    def __init__(self, existing=None):
        self.existing = dict(existing or {})
        self.created = []

    def connect(self, sandbox_id):
        if sandbox_id not in self.existing:
            raise FakeError("NOT_FOUND")
        if self.existing[sandbox_id] is None:
            raise FakeError("INVALID_ARGUMENT")
        return self.existing[sandbox_id]

    def create(self, *, sandbox_id, image):
        self.created.append((sandbox_id, image))
        self.existing[sandbox_id] = FakeBox()
        return self.existing[sandbox_id]


def _run(box, **args):
    args = {"language": "shell", "code": "true", **args}
    return run_code(args, sandbox_cls=FakeSandbox({SID: box}))


def test_runs_code_and_returns_stdout():
    box = FakeBox(result=FakeResult(stdout=b"hello\n"))

    assert _run(box, language="python", code="print('hello')") == "hello\n"
    assert box.calls == [
        (
            "print('hello')",
            {
                "language": "python",
                "envs": common.RUN_ENVS,
                "timeout": None,
            },
        )
    ]


def test_timeout_is_passed_through():
    box = FakeBox()

    _run(box, timeout=5)

    assert box.calls[0][1]["timeout"] == 5


def test_nonzero_exit_raises_with_stderr_and_stdout():
    box = FakeBox(result=FakeResult(exit_code=2, stdout=b"partial", stderr=b"bad"))

    with pytest.raises(SandboxToolError) as excinfo:
        _run(box)

    message = str(excinfo.value)
    assert "code 2" in message
    assert "bad" in message
    assert "partial" in message


def test_handler_is_async_and_returns_stdout(monkeypatch):
    seen = []
    monkeypatch.setattr(sandbox, "run_code", lambda args: seen.append(args) or "ok\n")
    args = {"language": "shell", "code": "echo ok"}

    result = asyncio.run(handle_sandbox(args, task_id="t"))

    assert result == "ok\n"
    assert seen == [args]


def test_open_creates_with_python_image_when_missing():
    cls = FakeSandbox()

    box = _open_sandbox(cls, "hermes-x")

    assert cls.created == [("hermes-x", common.DEFAULT_IMAGE)]
    assert box is cls.existing["hermes-x"]


def test_open_reconnects_to_open_sandbox():
    existing = FakeBox("open")
    cls = FakeSandbox({"hermes-x": existing})

    assert _open_sandbox(cls, "hermes-x") is existing
    assert cls.created == []


def test_open_resumes_suspended_sandbox():
    existing = FakeBox("suspended")
    cls = FakeSandbox({"hermes-x": existing})

    assert _open_sandbox(cls, "hermes-x") is existing
    assert existing.resumed


def test_open_skips_closed_ids_with_numeric_suffix():
    cls = FakeSandbox({"hermes-x": None, "hermes-x-1": None})

    _open_sandbox(cls, "hermes-x")

    assert [sid for sid, _ in cls.created] == ["hermes-x-2"]


def test_open_gives_up_after_max_generations(monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_GENERATIONS", 2)
    cls = FakeSandbox({"hermes-x": None, "hermes-x-1": None})

    with pytest.raises(SandboxToolError, match="could not open"):
        _open_sandbox(cls, "hermes-x")


def test_open_propagates_unexpected_errors():
    class Broken(FakeSandbox):
        def connect(self, sandbox_id):
            raise FakeError("INTERNAL")

    with pytest.raises(FakeError):
        _open_sandbox(Broken(), "hermes-x")


def test_handle_is_cached_across_calls():
    cls = FakeSandbox({SID: FakeBox()})
    for _ in range(3):
        run_code({"language": "shell", "code": "true"}, sandbox_cls=cls)
    box = cls.existing[SID]
    assert len(box.calls) == 3


def test_stale_handle_is_reopened_and_retried():
    class StaleBox(FakeBox):
        def run_code(self, code, **kwargs):
            raise FakeError("INVALID_ARGUMENT")

    sid = SID
    fresh = FakeBox(result=FakeResult())
    cls = FakeSandbox({sid: fresh})
    sandbox._HANDLES[sid] = StaleBox()
    run_code({"language": "shell", "code": "true"}, sandbox_cls=cls)
    assert len(fresh.calls) == 1
    assert sandbox._HANDLES[sid] is fresh


def test_language_defaults_to_python():
    box = FakeBox()

    run_code({"code": "x"}, sandbox_cls=FakeSandbox({SID: box}))

    assert box.calls[0][1]["language"] == "python"


def test_sandbox_id_is_stable_per_machine():
    assert SID == f"hermes-sandbox-{common.machine_id()}"


def test_runs_in_the_machine_sandbox():
    box = FakeBox(result=FakeResult(stdout=b"ok"))
    cls = FakeSandbox({SID: box})

    assert run_code({"language": "shell", "code": "true"}, sandbox_cls=cls) == "ok"
    assert cls.created == []


def test_creates_the_machine_sandbox_when_missing():
    cls = FakeSandbox()

    run_code({"language": "shell", "code": "x"}, sandbox_cls=cls)

    assert [sid for sid, _ in cls.created] == [SID]

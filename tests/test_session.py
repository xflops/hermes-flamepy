import asyncio
import json
from concurrent.futures import Future

import pytest

from hermes_flamepy import common, session
from hermes_flamepy.session import SessionToolError, run_codes


@pytest.fixture(autouse=True)
def _clear_handles():
    session._HANDLES.clear()


class FakeResult:
    def __init__(self, exit_code=0, stdout=b"", stderr=b""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    def stdout_text(self):
        return self.stdout.decode()

    def stderr_text(self):
        return self.stderr.decode()


class FakeError(Exception):
    def __init__(self, name):
        super().__init__(name)
        self.code = type("Code", (), {"name": name})


class FakeSession:
    def __init__(self, results=None, fail_submit=None):
        self.results = list(results or [])
        self.fail_submit = fail_submit
        self.calls = []
        self.closed = False

    def submit_code(self, code, **kwargs):
        if self.fail_submit:
            raise self.fail_submit
        self.calls.append((code, kwargs))
        future = Future()
        result = self.results.pop(0) if self.results else FakeResult(stdout=code.encode())
        if isinstance(result, Exception):
            future.set_exception(result)
        else:
            future.set_result(result)
        return future

    def close(self):
        self.closed = True


class FakeCluster:
    """Stands in for flamepy.agent.open_session; a None entry is a closed session."""

    def __init__(self, existing=None, fresh=None):
        self.existing = dict(existing or {})
        self.fresh = list(fresh or [])
        self.created = []

    def open_session(self, *, ssn_id, **options):
        if ssn_id not in self.existing:
            self.created.append((ssn_id, options))
            self.existing[ssn_id] = self.fresh.pop(0) if self.fresh else FakeSession()
        elif self.existing[ssn_id] is None:
            raise FakeError("INVALID_ARGUMENT")
        return self.existing[ssn_id]


def run(args, cluster):
    return run_codes(args, open_session=cluster.open_session)


def codes(*snippets):
    return {"codes": list(snippets), "language": "python"}


SID = common.flame_id("session")


def test_session_id_is_stable_per_machine():
    assert SID == f"hermes-session-{common.machine_id()}"


def test_runs_codes_and_reports_each_in_order():
    fake = FakeSession([FakeResult(stdout=b"a"), FakeResult(1, b"", b"boom")])

    results = run(codes("x", "y"), FakeCluster(fresh=[fake]))

    assert [r.exit_code for r in results] == [0, 1]
    assert results[1].stderr_text() == "boom"
    assert fake.calls[0][1]["language"] == "python"
    assert fake.calls[0][1]["envs"] == common.RUN_ENVS


def test_every_snippet_shares_language_and_timeout():
    fake = FakeSession()

    run({"codes": ["a", "b"], "language": "shell", "timeout": 5}, FakeCluster(fresh=[fake]))

    assert [(c, k["language"], k["timeout"]) for c, k in fake.calls] == [("a", "shell", 5), ("b", "shell", 5)]


def test_language_defaults_to_python():
    fake = FakeSession()

    run({"codes": ["a"]}, FakeCluster(fresh=[fake]))

    assert fake.calls[0][1]["language"] == "python"


def test_creates_the_machine_session():
    cluster = FakeCluster()

    run(codes("x"), cluster)

    (ssn_id, kwargs), = cluster.created
    assert ssn_id == SID
    assert kwargs == {"image": common.DEFAULT_IMAGE}


def test_reconnects_to_existing_session_after_restart():
    existing = FakeSession()
    cluster = FakeCluster({SID: existing})

    run(codes("x"), cluster)

    assert cluster.created == []
    assert existing.calls


def test_closed_session_moves_to_next_generation():
    cluster = FakeCluster({SID: None, SID + "-1": None})

    run(codes("x"), cluster)

    assert [sid for sid, _ in cluster.created] == [SID + "-2"]


def test_other_open_errors_propagate():
    cluster = FakeCluster()
    cluster.open_session = lambda **kwargs: (_ for _ in ()).throw(FakeError("UNAVAILABLE"))

    with pytest.raises(FakeError):
        run(codes("x"), cluster)


def test_exception_in_one_snippet_does_not_fail_others():
    fake = FakeSession([RuntimeError("lost"), FakeResult(stdout=b"ok")])

    results = run(codes("x", "y"), FakeCluster(fresh=[fake]))

    assert str(results[0]) == "lost"
    assert results[1].stdout_text() == "ok"


def test_reuses_handle_across_calls():
    cluster = FakeCluster()

    run(codes("x"), cluster)
    run(codes("y"), cluster)

    assert len(cluster.created) == 1


def test_reopens_once_when_handle_is_stale():
    stale = FakeSession()
    cluster = FakeCluster(fresh=[stale])
    run(codes("x"), cluster)
    stale.fail_submit = FakeError("INVALID_STATE")
    cluster.existing[SID] = None  # the cluster now reports it closed

    results = run(codes("y"), cluster)

    assert stale.closed
    assert results[0].stdout_text() == "y"
    assert [sid for sid, _ in cluster.created] == [SID, SID + "-1"]


def test_non_stale_submit_error_propagates():
    broken = FakeSession(fail_submit=FakeError("UNAVAILABLE"))

    with pytest.raises(FakeError):
        run(codes("x"), FakeCluster(fresh=[broken]))


def test_handler_returns_json_for_results_and_failures(monkeypatch):
    results = [RuntimeError("lost"), FakeResult(1, b"o", b"e"), FakeResult(0, b"x")]
    seen = []
    monkeypatch.setattr(session, "run_codes", lambda args: seen.append(args) or results)

    out = asyncio.run(session.handle_session(codes("a", "b", "c"), task_id="t"))

    assert seen == [codes("a", "b", "c")]
    assert json.loads(out) == [
        {"exit_code": -1, "stdout": "", "stderr": "lost"},
        {"exit_code": 1, "stdout": "o", "stderr": "e"},
        {"exit_code": 0, "stdout": "x", "stderr": ""},
    ]


@pytest.mark.parametrize(
    "args",
    [
        {"language": "python"},
        {"codes": [], "language": "python"},
        {"codes": ["x", 1], "language": "python"},
        {"codes": ["x"], "language": "ruby"},
    ],
)
def test_rejects_invalid_args(args):
    with pytest.raises(SessionToolError):
        run(args, FakeCluster())

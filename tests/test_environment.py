"""Real BaseEnvironment commands, file I/O, shell state and restart behavior."""

from concurrent.futures import Future

import pytest

from tools.environments.base import EnvironmentConnectionError


def test_shell_state_files_and_stdin_survive_reconnect(make_env, sdk):
    env = make_env()
    result = env.execute("mkdir -p 'project space'; cd 'project space'; export ANSWER=42; printf persisted > data")
    assert result["returncode"] == 0
    first_id = env.sandbox_id
    # A fresh controller object reconnects without a prior cleanup/suspension.
    assert sdk.handles[first_id].close_calls == 0
    reconnected = make_env()
    assert reconnected.sandbox_id == first_id
    assert reconnected.cwd.endswith("project space")
    result = reconnected.execute('printf "%s:" "$ANSWER"; cat data; cat', stdin_data="\nstdin\x00é\n")
    assert result["returncode"] == 0
    assert result["output"] == "42:persisted\nstdin\x00é\n"
    assert len(sdk.created) == 1


def test_cleanup_suspends_and_reconnect_resumes_same_id(make_env, sdk):
    env = make_env()
    assert env.execute("export ANSWER=42; printf persisted > data")["returncode"] == 0
    handle = sdk.handles[env.sandbox_id]
    env.cleanup()
    env.cleanup()
    assert handle.state == "suspended"
    assert handle.close_calls == 0
    assert handle.suspend_calls == 1
    reconnected = make_env()
    assert reconnected.sandbox_id == env.sandbox_id
    result = reconnected.execute('test ! -f data && test -z "$ANSWER"')
    assert result["returncode"] == 0
    assert result["environment_recreated"] is True
    assert handle.resume_calls == 1
    assert len(sdk.created) == 1


def test_file_tools_roundtrip_bytes_without_host_file_access(make_env, tmp_path):
    env = make_env()
    payload = "a\r\nb\x00é\nlast line"
    assert env.execute("cat > payload", stdin_data=payload)["returncode"] == 0
    assert env.execute("cat payload")["output"] == payload
    target = tmp_path / "download"
    env.fetch_file(env.cwd + "/payload", target, max_bytes=100)
    assert target.read_bytes() == payload.encode()
    assert env.uses_host_filesystem is False


def test_output_order_exit_status_and_large_result(make_env):
    env = make_env()
    result = env.execute("printf out; printf err >&2; printf end; exit 7")
    assert result["returncode"] == 7
    assert result["output"] == "outerrend"
    result = env.execute("python3 -c 'print(\"x\" * 300000, end=\"\")'")
    assert result["returncode"] == 0
    assert result["output"] == "x" * 300000


def test_explicit_workdir_does_not_replace_saved_cwd(make_env):
    env = make_env()
    env.execute("mkdir project; cd project")
    expected = env.cwd
    env.execute("pwd", cwd="/")
    assert make_env().cwd == expected


def test_ephemeral_environment_is_unique_and_cleanup_is_idempotent(make_env, sdk):
    first = make_env(persistent=False)
    second = make_env(persistent=False)
    assert first.sandbox_id != second.sandbox_id
    first.cleanup()
    first.cleanup()
    assert sdk.handles[first.sandbox_id].close_calls == 1
    with pytest.raises(EnvironmentConnectionError, match="terminated"):
        first.execute("true")


def test_closed_sandbox_gets_new_generation_and_notice(make_env, sdk):
    env = make_env()
    sdk.handles[env.sandbox_id].close()
    replacement = make_env()
    assert replacement.sandbox_id == env.sandbox_id + "-1"
    assert replacement.execute("true")["environment_recreated"] is True
    assert "environment_recreated" not in replacement.execute("true")


def test_failed_remote_result_is_not_replayed(make_env, sdk):
    env = make_env()
    handle = sdk.handles[env.sandbox_id]
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        future = Future()
        future.set_exception(ConnectionError("connection lost after submission"))
        return future

    handle.submit_code = fail
    with pytest.raises(EnvironmentConnectionError, match="connection lost"):
        env.execute("printf side-effect")
    assert len(calls) == 1
    assert len(sdk.created) == 1


def test_interrupt_suspends_persistent_sandbox(make_env, sdk):
    from tools.interrupt import set_interrupt

    env = make_env()
    set_interrupt(True)
    try:
        result = env.execute("sleep 0.1")
    finally:
        set_interrupt(False)
    assert result["returncode"] == 130
    assert sdk.handles[env.sandbox_id].state == "suspended"
    assert make_env().sandbox_id == env.sandbox_id


def test_sdk_timeout_is_forwarded(make_env, sdk):
    env = make_env()
    proc = env._run_bash("sleep 5", timeout=0.1)
    result = env._wait_for_process(proc, timeout=2)
    assert result["returncode"] == 124
    assert sdk.handles[env.sandbox_id].calls[-1][1]["timeout"] == 0.1


def test_cancellation_detaches_while_suspend_drains(make_env, sdk, monkeypatch):
    import flamepy.core
    from hermes_flamepy import store
    from hermes_flamepy.process import FlameProcess

    env = make_env()
    handle = sdk.handles[env.sandbox_id]
    monkeypatch.setattr(flamepy.core, "suspend_session", lambda _: setattr(handle, "state", "suspending"))
    proc = FlameProcess(Future, lambda: env._terminate(wait=False))
    proc.kill()
    assert proc.wait(1) == -9
    assert handle.state == "suspending"
    assert handle.close_calls == 0
    assert not env.cleanup_stops_processes
    monkeypatch.setattr(store, "sleep", lambda _: setattr(handle, "state", "suspended"))
    assert make_env().sandbox_id == env.sandbox_id
    assert len(sdk.created) == 1
    proc.stdout.close()


def test_native_home_and_package_configuration(make_env, sdk):
    env = make_env()
    result = env.execute('test -d "$HOME" && test -d "$XDG_CACHE_HOME"')
    assert result["returncode"] == 0
    forwarded = sdk.handles[env.sandbox_id].calls[-1][1]["envs"]
    assert "PIP_TARGET" not in forwarded
    assert "PYTHONPATH" not in forwarded


def test_failed_cancellation_can_be_retried_by_cleanup(make_env, sdk, monkeypatch):
    from hermes_flamepy.process import FlameProcess

    env = make_env()
    handle = sdk.handles[env.sandbox_id]
    original = handle.suspend
    attempts = []

    def suspend():
        attempts.append(True)
        if len(attempts) == 1:
            raise ConnectionError("suspend RPC failed")
        original()

    monkeypatch.setattr(handle, "suspend", suspend)
    proc = FlameProcess(Future, env._terminate)
    with pytest.raises(ConnectionError, match="suspend RPC failed"):
        proc.kill()
    assert proc.poll() is None
    env.cleanup()
    assert handle.state == "suspended"
    assert not env.cleanup_stops_processes
    proc.kill()
    assert proc.wait(1) == -9
    proc.stdout.close()


@pytest.mark.parametrize("path", ["new project", "Users/klaus/project", "nested/'quotes'"])
def test_creates_missing_initial_working_directory(make_env, tmp_path, path):
    cwd = str(tmp_path / "remote" / path)
    env = make_env(cwd=cwd)
    result = env.execute("pwd -P")
    assert result["returncode"] == 0
    assert result["output"].strip() == cwd


def test_home_relative_working_directory_is_remote(make_env, tmp_path):
    env = make_env(cwd="~/project")
    assert env.cwd == str(tmp_path / "remote/project")


def test_relative_working_directory_is_rejected_before_creating_sandbox(make_env, sdk):
    with pytest.raises(ValueError, match="absolute POSIX path"):
        make_env(cwd="relative/project")
    assert not sdk.created

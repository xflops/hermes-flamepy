"""Hermes identity scoping and safe connect/create decisions."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_flamepy.store import open_sandbox


def test_identity_profile_and_image_isolation(tmp_path, sdk):
    def open_for(task="a", image="image:a", profile="one"):
        return open_sandbox(sdk, state_dir=tmp_path / profile, task_id=task, image=image, persistent=True)[0]

    first = open_for()
    assert open_for().id == first.id
    assert len({first.id, open_for(task="b").id, open_for(image="image:b").id,
                open_for(profile="two").id}) == 4


def test_concurrent_create_uses_one_sandbox(tmp_path, sdk):
    def open_one(_):
        return open_sandbox(sdk, state_dir=tmp_path, task_id="same", image="image", persistent=True)[0].id

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(open_one, range(4)))
    assert len(set(ids)) == 1
    assert len(sdk.created) == 1


def test_connection_failure_does_not_create_replacement(tmp_path, sdk, monkeypatch):
    import flamepy.core

    def fail(_):
        raise ConnectionError("cluster down")

    monkeypatch.setattr(flamepy.core, "get_session", fail)
    with pytest.raises(ConnectionError, match="cluster down"):
        open_sandbox(sdk, state_dir=tmp_path, task_id="same", image="image", persistent=True)
    assert not sdk.created


def test_suspended_sandbox_reports_state_loss(tmp_path, sdk):
    kwargs = dict(state_dir=tmp_path, task_id="same", image="image", persistent=True)
    sandbox, _ = open_sandbox(sdk, **kwargs)
    sandbox.state = "suspended"
    resumed, recreated = open_sandbox(sdk, **kwargs)
    assert resumed.id == sandbox.id
    assert recreated
    assert resumed.resume_calls == 1


def test_reconnect_waits_for_inflight_suspension(tmp_path, sdk, monkeypatch):
    from hermes_flamepy import store

    kwargs = dict(state_dir=tmp_path, task_id="same", image="image", persistent=True)
    sandbox, _ = open_sandbox(sdk, **kwargs)
    sandbox.state = "suspending"
    monkeypatch.setattr(store, "sleep", lambda _: setattr(sandbox, "state", "suspended"))
    resumed, recreated = open_sandbox(sdk, **kwargs)
    assert resumed.id == sandbox.id
    assert recreated
    assert sandbox.suspend_calls == 0
    assert sandbox.resume_calls == 1
    assert len(sdk.created) == 1


def test_pending_suspend_is_not_replaced_on_timeout(tmp_path, sdk, monkeypatch):
    from hermes_flamepy import store

    kwargs = dict(state_dir=tmp_path, task_id="same", image="image", persistent=True)
    sandbox, _ = open_sandbox(sdk, **kwargs)
    sandbox.state = "suspending"
    clock = iter([0, 61])
    monkeypatch.setattr(store, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="still suspending"):
        open_sandbox(sdk, **kwargs)
    assert len(sdk.created) == 1

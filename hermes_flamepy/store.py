"""Reconnect Hermes environment identities to open Flame sandboxes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import monotonic, sleep
import uuid

from filelock import FileLock
from flamepy import core
from flamepy.core.types import SessionState


def _error_code(exc):
    return getattr(getattr(exc, "code", None), "name", None)


def _session_state(sandbox_id):
    session = core.get_session(sandbox_id)
    if session.application != "flmbox":
        raise ValueError(f"Flame session {sandbox_id} belongs to {session.application}, not flmbox")
    return SessionState(session.state).name.lower()


def wait_for_suspension(sandbox_id, timeout=60):
    """Wait without signalling again: Flame rejects a repeated suspend signal."""
    deadline = monotonic() + timeout
    while True:
        state = _session_state(sandbox_id)
        if state == "suspended":
            return
        if state != "suspending":
            raise RuntimeError(f"Flame sandbox {sandbox_id} became {state} while suspending")
        if monotonic() >= deadline:
            raise TimeoutError(f"Flame sandbox {sandbox_id} is still suspending; retry after remote work drains")
        sleep(0.2)


def suspend_sandbox(sandbox, *, wait=True):
    """Request suspension; cancellation can detach while remote work drains."""
    state = sandbox.state
    if state == "closed":
        return
    if state == "open":
        try:
            core.suspend_session(sandbox.id)
        except Exception as exc:
            if _error_code(exc) != "INVALID_STATE" or sandbox.state not in {"suspending", "suspended"}:
                raise
    elif state not in {"suspending", "suspended"}:
        raise RuntimeError(f"Cannot suspend Flame sandbox {sandbox.id} in state {state}")
    if wait:
        wait_for_suspension(sandbox.id)


def _identity(state_dir, task_id, image):
    profile = hashlib.sha256(str(state_dir.resolve()).encode()).hexdigest()[:16]
    identity = hashlib.sha256(json.dumps([str(task_id), image]).encode()).hexdigest()[:24]
    return f"hermes-{profile}-{identity}", state_dir / f"terminal-{identity}.json"


def open_sandbox(sandbox_cls, *, state_dir, task_id, image, persistent):
    """Return (sandbox, recreated). Never retry an actual user command.

    The lock protects the connect/create decision across Hermes processes.
    Closed IDs cannot be reused in Flame, so a replacement gets a generation
    suffix. The record only contains a generation, never credentials.
    """
    if not persistent:
        return sandbox_cls.create(sandbox_id=f"hermes-{uuid.uuid4().hex}", image=image), False
    state_dir = Path(state_dir)
    base_id, record = _identity(state_dir, task_id, image)
    state_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(record) + ".lock", timeout=60):
        try:
            generation = json.loads(record.read_text(encoding="utf-8"))["generation"]
        except FileNotFoundError:
            generation = 0
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
            raise ValueError(f"Invalid Flame sandbox generation in {record}")
        had_record = record.exists()
        for offset in range(32):
            current = generation + offset
            sandbox_id = base_id if current == 0 else f"{base_id}-{current}"
            try:
                # SDK connect uses OpenSession, which rejects suspended as
                # well as closed sessions. Read metadata before attaching.
                state = _session_state(sandbox_id)
            except Exception as exc:
                if _error_code(exc) != "NOT_FOUND":
                    raise
                sandbox = sandbox_cls.create(sandbox_id=sandbox_id, image=image)
                _save_generation(record, current)
                return sandbox, had_record or offset > 0
            if state == "open":
                sandbox = sandbox_cls.connect(sandbox_id)
                _save_generation(record, current)
                return sandbox, offset > 0
            if state in {"suspending", "suspended"}:
                if state == "suspending":
                    # A previous suspend request can still be draining work.
                    # Wait for it; never replace a live session with a new ID.
                    wait_for_suspension(sandbox_id)
                core.resume_session(sandbox_id)
                sandbox = sandbox_cls.connect(sandbox_id)
                _save_generation(record, current)
                return sandbox, True
            if state != "closed":
                raise RuntimeError(f"Flame sandbox {sandbox_id} is {state}; retry after its transition completes")
    raise RuntimeError(f"Too many closed Flame sandbox generations for {base_id}")


def _save_generation(record, generation):
    temporary = record.with_suffix(".tmp")
    temporary.write_text(json.dumps({"generation": generation}) + "\n", encoding="utf-8")
    temporary.replace(record)

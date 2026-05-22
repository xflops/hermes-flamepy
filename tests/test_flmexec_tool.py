import json

import pytest

from hermes_flamepy.flmexec_tool import (
    FlmexecToolError,
    _decode_script_output,
    _encode_script_request,
    _resolve_hermes_session_id,
    handle_flmexec,
    run_flmexec,
)


class FakeAttrs:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeSession:
    def __init__(self, session_id, output):
        self.id = session_id
        self.output = output
        self.inputs = []

    def invoke(self, payload):
        self.inputs.append(payload)
        return self.output


def _opener(output):
    calls = []

    def open_session(session_id, attrs):
        calls.append((session_id, attrs))
        return FakeSession(session_id, output)

    open_session.calls = calls
    return open_session


def test_uses_hermes_session_id_to_open_flmexec_session():
    output = json.dumps({"data": list(b"hello\n")}).encode()
    open_session = _opener(output)

    result = run_flmexec(
        {"language": "python", "code": "print('hello')"},
        session_id="hermes-123",
        open_session_fn=open_session,
        session_attrs_cls=FakeAttrs,
    )

    assert result == "hello\n"
    assert open_session.calls[0][0] == "hermes-123"
    assert open_session.calls[0][1].kwargs["id"] == "hermes-123"
    assert open_session.calls[0][1].kwargs["application"] == "flmexec"


def test_encodes_script_request_as_flame_message_json():
    payload = _encode_script_request("shell", "echo hello")

    assert json.loads(payload.decode()) == {
        "language": "shell",
        "code": "echo hello",
        "input": None,
    }


def test_decodes_script_output_flame_message_json():
    raw = json.dumps({"data": [226, 130, 172]}).encode()

    assert _decode_script_output(raw) == "€"


def test_handler_returns_plain_stdout():
    output = json.dumps({"data": list(b"ok\n")}).encode()

    assert (
        handle_flmexec(
            {"language": "shell", "code": "echo ok"},
            session_id="sid",
            open_session_fn=_opener(output),
            session_attrs_cls=FakeAttrs,
        )
        == "ok\n"
    )


def test_rejects_non_utf8_output():
    raw = json.dumps({"data": [255]}).encode()

    with pytest.raises(FlmexecToolError, match="UTF-8"):
        _decode_script_output(raw)


def test_resolves_session_id_from_agent_on_stack():
    class Agent:
        session_id = "stack-session"

    def call_with_agent_local():
        agent = Agent()
        return _resolve_hermes_session_id({}, {})

    assert call_with_agent_local() == "stack-session"


def test_missing_session_id_raises():
    with pytest.raises(FlmexecToolError, match="session_id"):
        handle_flmexec(
            {"language": "python", "code": "print(1)"},
            open_session_fn=_opener(b""),
            session_attrs_cls=FakeAttrs,
        )


def test_rejects_invalid_language():
    with pytest.raises(FlmexecToolError):
        run_flmexec(
            {"language": "ruby", "code": "puts 1"},
            session_id="sid",
            open_session_fn=_opener(b""),
            session_attrs_cls=FakeAttrs,
        )


def test_rejects_code_that_cannot_encode_as_utf8():
    with pytest.raises(FlmexecToolError, match="UTF-8"):
        run_flmexec(
            {"language": "python", "code": "\udcff"},
            session_id="sid",
            open_session_fn=_opener(b""),
            session_attrs_cls=FakeAttrs,
        )

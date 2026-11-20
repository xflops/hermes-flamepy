"""Only a terminal provider is exposed to Hermes."""

from types import SimpleNamespace

import pytest

from hermes_flamepy import register


def test_registers_only_terminal_backend(tmp_path):
    providers = []
    ctx = SimpleNamespace(
        register_terminal_environment_provider=providers.append,
        get_config=lambda key, default=None: default,
        state=SimpleNamespace(data_dir=tmp_path),
    )
    register(ctx)
    assert len(providers) == 1
    provider = providers[0]
    assert provider.name == "flamepy"
    assert provider.is_remote and provider.is_container
    assert provider.is_available()
    assert not provider.skip_container_guards
    assert not provider.supports_streams


def test_old_hermes_has_actionable_error():
    with pytest.raises(RuntimeError, match="update Hermes"):
        register(SimpleNamespace())


def test_provider_uses_settings_and_factory_overrides(tmp_path, monkeypatch):
    from hermes_flamepy.environment import FlameEnvironment
    from hermes_flamepy.provider import FlameProvider

    calls = []
    monkeypatch.setattr(FlameEnvironment, "__init__", lambda self, **kw: calls.append(kw))
    ctx = SimpleNamespace(get_config=lambda key, default: "custom:image", state=SimpleNamespace(data_dir=tmp_path))
    provider = FlameProvider(ctx)
    provider.create_environment(cwd="/tmp", timeout=30, task_id="session-1", future_option=True)
    assert calls[-1]["image"] == "custom:image"
    assert calls[-1]["task_id"] == "session-1"
    provider.create_environment(cwd="/tmp", timeout=30, image="override:image",
                                container_config={"container_persistent": False})
    assert calls[-1]["image"] == "override:image"
    assert calls[-1]["persistent"] is False


def test_availability_does_not_contact_cluster(monkeypatch):
    import flamepy.agent
    from hermes_flamepy.provider import FlameProvider

    monkeypatch.setattr(flamepy.agent.Sandbox, "create", lambda **_: pytest.fail("network call"))
    monkeypatch.setattr(flamepy.agent.Sandbox, "connect", lambda *_: pytest.fail("network call"))
    assert FlameProvider(None).is_available()


def test_provider_defaults_to_hermes_cwd(monkeypatch, tmp_path):
    from hermes_flamepy.provider import FlameProvider

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)
    assert FlameProvider(None).default_cwd == str(tmp_path)


def test_configured_host_path_survives_hermes_container_sanitization(monkeypatch):
    from hermes_flamepy.provider import FlameProvider
    from agent.terminal_env_registry import register_provider
    from tools.terminal_tool import _resolve_config_cwd

    monkeypatch.setenv("TERMINAL_CWD", "/Users/klaus/project")
    register_provider(FlameProvider(None))
    assert _resolve_config_cwd("flamepy", False) == ("/Users/klaus/project", None)

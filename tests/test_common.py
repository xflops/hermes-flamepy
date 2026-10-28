import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REAL_MACHINE_ID
from hermes_flamepy import common

HARDWARE_PORTS = """Hardware Port: Wi-Fi
Device: en0
Ethernet Address: 6A:55:CD:6B:DE:31

Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: 36:04:43:b8:4d:c0
"""


@pytest.fixture(autouse=True)
def _real_machine_id(monkeypatch):
    monkeypatch.setattr(common, "machine_id", REAL_MACHINE_ID)
    REAL_MACHINE_ID.cache_clear()
    yield
    REAL_MACHINE_ID.cache_clear()


def _linux(monkeypatch, tmp_path, interfaces):
    """A fake /sys/class/net; an interface is physical when it has a `device` entry."""
    for name, (address, physical) in interfaces.items():
        iface = tmp_path / name
        iface.mkdir()
        (iface / "address").write_text(address + "\n")
        if physical:
            (iface / "device").mkdir()
    real_path = common.Path
    monkeypatch.setattr(common, "Path", lambda p: tmp_path if p == "/sys/class/net" else real_path(p))


def test_linux_uses_the_first_physical_interface(monkeypatch, tmp_path):
    _linux(monkeypatch, tmp_path, {
        "docker0": ("02:42:ac:11:00:01", False),
        "enp3s0": ("AA:BB:CC:00:00:02", True),
        "enp1s0": ("aa:bb:cc:00:00:01", True),
        "lo": ("00:00:00:00:00:00", False),
    })

    assert common.machine_id() == "aabbcc000001"


def test_linux_without_physical_interface_is_an_error(monkeypatch, tmp_path):
    _linux(monkeypatch, tmp_path, {"lo": ("00:00:00:00:00:00", False), "veth1": ("02:00:00:00:00:01", False)})

    with pytest.raises(RuntimeError, match="physical"):
        common.machine_id()


def test_zero_address_of_a_physical_interface_is_skipped(monkeypatch, tmp_path):
    _linux(monkeypatch, tmp_path, {"eth0": ("00:00:00:00:00:00", True), "eth1": ("aa:bb:cc:00:00:09", True)})

    assert common.machine_id() == "aabbcc000009"


@pytest.mark.skipif(sys.platform == "linux", reason="needs a non-Linux host")
def test_macos_uses_the_first_hardware_port(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=HARDWARE_PORTS)
    )

    assert common.machine_id() == "6a55cd6bde31"


@pytest.mark.skipif(sys.platform == "linux", reason="needs a non-Linux host")
def test_macos_without_networksetup_is_an_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(RuntimeError, match="physical"):
        common.machine_id()


def test_machine_id_is_stable_between_calls():
    assert common.machine_id() == common.machine_id()

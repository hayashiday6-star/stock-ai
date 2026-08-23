"""The Tachibana probe's command line, exercised through ``main()``.

These exist because of a specific failure. The session cache was added to
``probe()`` and verified by calling ``probe()`` directly - which passed - while
the edit wiring the new arguments into ``main()`` never landed. Nothing caught
it: ruff cannot see a missing keyword argument, and the only test path skipped
the entry point. The first real run died with

    TypeError: probe() missing 1 required keyword-only argument: 'session_path'

after the user had already registered a key and generated an auth ID.

So the rule here is that the command line is tested the way it is invoked.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent.parent / "tools" / "tachibana_probe.py"


def _module():
    """Import the probe by path; ``tools/`` is not a package."""
    spec = importlib.util.spec_from_file_location("tachibana_probe", _PROBE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def probe_module():
    """The probe module, imported fresh."""
    return _module()


def test_main_passes_every_argument_probe_requires(probe_module, monkeypatch, tmp_path):
    """``main()`` must satisfy ``probe()``'s full signature, not most of it."""
    captured: dict = {}

    def fake_probe(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(probe_module, "probe", fake_probe)
    monkeypatch.setenv("TACHIBANA_AUTH_ID", "authid")
    monkeypatch.setattr(sys, "argv", ["tachibana_probe.py", "probe"])

    assert probe_module.main() == 0

    # Bind the real signature to what main() actually passed. A missing or
    # misspelled argument fails here rather than on the user's first run.
    import inspect

    real_probe = _module().probe
    inspect.signature(real_probe).bind(*captured["args"], **captured["kwargs"])


def test_probe_arguments_reach_the_call(probe_module, monkeypatch, tmp_path):
    """Each flag has to change the call, not merely be accepted by the parser."""
    captured: dict = {}
    monkeypatch.setattr(probe_module, "probe", lambda *a, **k: captured.update(k) or 0)
    monkeypatch.setenv("TACHIBANA_AUTH_ID", "authid")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tachibana_probe.py",
            "probe",
            "--symbol",
            "7203",
            "--get",
            "--fresh",
            "--session",
            str(tmp_path / "s.json"),
        ],
    )

    assert probe_module.main() == 0
    assert captured["use_post"] is False
    assert captured["fresh"] is True
    assert captured["session_path"] == tmp_path / "s.json"


def test_demo_switches_the_host(probe_module, monkeypatch):
    """``--demo`` must reach the demo host, not the production one."""
    captured: dict = {}
    monkeypatch.setattr(
        probe_module, "probe", lambda base, *a, **k: captured.update(base=base) or 0
    )
    monkeypatch.setenv("TACHIBANA_AUTH_ID", "authid")
    monkeypatch.setattr(sys, "argv", ["tachibana_probe.py", "probe", "--demo"])

    assert probe_module.main() == 0
    assert captured["base"] == probe_module._DEMO
    assert "demo" in captured["base"]


def test_a_missing_auth_id_stops_before_any_request(probe_module, monkeypatch):
    """An empty auth ID is a setup problem, and saying so beats a 401."""
    monkeypatch.setattr(probe_module, "probe", lambda *a, **k: pytest.fail("should not have run"))
    monkeypatch.setenv("TACHIBANA_AUTH_ID", "   ")
    monkeypatch.setattr(sys, "argv", ["tachibana_probe.py", "probe"])

    with pytest.raises(SystemExit) as excinfo:
        probe_module.main()
    assert "TACHIBANA_AUTH_ID" in str(excinfo.value)


def test_keygen_writes_both_files(probe_module, monkeypatch, tmp_path, capsys):
    """``keygen`` is reached through the same entry point as ``probe``."""
    private = tmp_path / "k.pem"
    public = tmp_path / "k.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        ["tachibana_probe.py", "keygen", "--private", str(private), "--public", str(public)],
    )

    assert probe_module.main() == 0
    assert private.exists() and public.exists()
    body = public.read_text(encoding="utf-8")
    # Format 1 is the one the settings page accepts; it must be present and first.
    assert "-----BEGIN PUBLIC KEY-----" in body
    assert body.index("BEGIN PUBLIC KEY") < body.index("BEGIN RSA PUBLIC KEY")
    # The private key must never be in the file meant for pasting into a form.
    assert "PRIVATE KEY" not in body


def test_keyfmt_does_not_touch_the_private_key(probe_module, monkeypatch, tmp_path):
    """Re-emitting formats must not invalidate an already-registered key."""
    private = tmp_path / "k.pem"
    public = tmp_path / "k.txt"
    probe_module.keygen(private, public)
    before = private.read_bytes()

    monkeypatch.setattr(
        sys,
        "argv",
        ["tachibana_probe.py", "keyfmt", "--private", str(private), "--public", str(public)],
    )
    assert probe_module.main() == 0
    assert private.read_bytes() == before

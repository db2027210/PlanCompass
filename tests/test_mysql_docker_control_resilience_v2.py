from __future__ import annotations

import subprocess

from scripts import mysql_docker_control_resilience_v2 as subject


def test_install_covers_measurement_prepare_entrypoint() -> None:
    original = subject.prep._docker_json
    try:
        subject.install()
        assert subject.prep._docker_json is subject.resilient_prep_json
    finally:
        subject.uninstall()
    assert subject.prep._docker_json is original


def test_measurement_inspect_retries_timeout(monkeypatch) -> None:
    attempts = 0

    def fake_json(*argv: str):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise subprocess.TimeoutExpired(["docker", *argv], 30)
        return {"State": {"Running": True}}

    monkeypatch.setattr(subject, "_ORIGINAL_PREP_JSON", fake_json)
    monkeypatch.setattr(subject.prior, "_serialized", lambda call: call())
    monkeypatch.setattr(subject.time, "sleep", lambda seconds: None)
    assert subject.resilient_prep_json("inspect", "worker") == {
        "State": {"Running": True}
    }
    assert attempts == 3

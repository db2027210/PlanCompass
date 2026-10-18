from __future__ import annotations

import subprocess

from scripts import wait_prepare_launch_mysql_c_async_after_b_v1 as handoff


def test_unit_state_treats_transient_systemctl_timeout_as_unknown(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=15)

    monkeypatch.setattr(handoff.subprocess, "run", timeout)

    assert handoff.unit_state("example.service") == "unknown"


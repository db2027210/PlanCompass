#!/usr/bin/env python3
"""Regression checks for slow-sudo and cached matrix VM readiness."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "shell"
for search_path in (ROOT, SHELL):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

import provision_double_loop_matrix_direct_qemu as direct  # noqa: E402
import provision_double_loop_matrix_vms as matrix  # noqa: E402
import rebuild_matrix_seeded_qemu as rebuild  # noqa: E402


def main() -> int:
    with mock.patch.object(matrix.subprocess, "run") as mocked_run:
        mocked_run.return_value = subprocess.CompletedProcess(
            args=["sudo", "-n", "true"],
            returncode=0,
            stdout="",
        )
        matrix.run(["sudo", "-n", "true"], timeout=10.0)
        assert mocked_run.call_args.kwargs["timeout"] == matrix.SUDO_MIN_TIMEOUT_S

        matrix.run(["ps", "-eo", "pid"], timeout=10.0)
        assert mocked_run.call_args.kwargs["timeout"] == 10.0

    with TemporaryDirectory() as raw:
        status_path = Path(raw) / "virbr0.status"
        status_path.write_text('[{"mac-address":"52:54:00:00:00:01"}]', encoding="utf-8")
        with mock.patch.object(matrix.Path, "__new__", return_value=status_path):
            with mock.patch.object(matrix, "run", side_effect=AssertionError("DHCP read must not call sudo")):
                assert matrix.dhcp_rows()[0]["mac-address"] == "52:54:00:00:00:01"

    a13 = next(spec for spec in matrix.vm_specs() if spec.exp_id == "A13")
    assert direct.candidate_ips(a13, []) == ["192.0.2.19"]

    b4_r2 = next(
        spec
        for spec in matrix.vm_specs()
        if spec.exp_id == "B4" and spec.repeat_id == 2
    )
    dynamic = [{"mac-address": b4_r2.mac, "ip-address": "192.0.2.47"}]
    assert direct.candidate_ips(b4_r2, dynamic) == ["192.0.2.47"]

    ready_row = {
        "ip": "192.0.2.19",
        "ssh_sudo": True,
        "postgres": "17",
        "memory_capacity_gib": 1.0,
    }
    with mock.patch.object(
        direct,
        "dhcp_rows",
        side_effect=AssertionError("static A/E readiness must not read DHCP"),
    ):
        with mock.patch.object(direct, "probe_ready", return_value=ready_row):
            assert direct.wait_ready([a13], 1.0)[a13.name] == ready_row

    fake_cmdline = f"qemu-system-x86_64 -name {a13.name} {a13.overlay}".encode("utf-8")

    def fake_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if str(path).endswith(".pid"):
            return "4321"
        if str(path).endswith("/status"):
            return "Name:\tqemu\nState:\tS (sleeping)\n"
        raise AssertionError(f"unexpected read: {path}")

    with mock.patch.object(direct.Path, "exists", return_value=True), mock.patch.object(
        direct.Path,
        "read_text",
        new=fake_read_text,
    ), mock.patch.object(direct.Path, "read_bytes", return_value=fake_cmdline), mock.patch.object(
        direct,
        "run",
        side_effect=AssertionError("direct_running must not use sudo/run for /proc polling"),
    ):
        assert direct.direct_running(a13)

    stop_calls = []

    def record_run(command, **kwargs):  # type: ignore[no-untyped-def]
        stop_calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="")

    with mock.patch.object(rebuild, "direct_running", return_value=True), mock.patch.object(
        rebuild,
        "read_root_file",
        return_value="4321",
    ), mock.patch.object(rebuild, "process_active", return_value=False), mock.patch.object(
        rebuild,
        "run",
        side_effect=record_run,
    ):
        rebuild.stop_owned_guest(a13)
    assert stop_calls == [["sudo", "-n", "kill", "-TERM", "4321"]]

    print("matrix direct-QEMU control-plane tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Unit checks for transient PostgreSQL recovery retries during snapshot restore."""

from __future__ import annotations

import subprocess

import bonect_parallel_vm_replay as replay


def main() -> int:
    original_run = replay.subprocess.run
    original_sleep = replay.time.sleep
    responses = [
        subprocess.CompletedProcess(
            ["psql"],
            2,
            stdout="",
            stderr="FATAL: the database system is not yet accepting connections",
        ),
        subprocess.CompletedProcess(["psql"], 0, stdout="", stderr=""),
    ]

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return responses.pop(0)

    replay.subprocess.run = fake_run
    replay.time.sleep = lambda _: None
    try:
        result, attempts = replay.run_snapshot_restore_with_recovery_retry(
            ["psql"], timeout_s=30.0
        )
    finally:
        replay.subprocess.run = original_run
        replay.time.sleep = original_sleep
    assert result.returncode == 0
    assert attempts == 2

    calls = []

    def permanent_failure(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        return subprocess.CompletedProcess(
            ["psql"], 1, stdout="", stderr="ERROR: template database does not exist"
        )

    replay.subprocess.run = permanent_failure
    replay.time.sleep = lambda _: None
    try:
        result, attempts = replay.run_snapshot_restore_with_recovery_retry(
            ["psql"], timeout_s=30.0
        )
    finally:
        replay.subprocess.run = original_run
        replay.time.sleep = original_sleep
    assert result.returncode == 1
    assert attempts == 1
    assert len(calls) == 1
    print("snapshot restore recovery retry tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

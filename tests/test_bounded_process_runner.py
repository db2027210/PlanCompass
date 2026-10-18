from __future__ import annotations

import os
import signal
import sys
import time

import pytest

import bounded_process_runner
from bounded_process_runner import (
    BoundedProcessCleanupError,
    BoundedProcessTimeoutError,
    run_bounded_process,
)


def test_requires_positive_finite_timeout() -> None:
    for value in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            run_bounded_process([sys.executable, "-c", "pass"], timeout_s=value)


def test_streams_and_captures_output() -> None:
    seen = []
    result = run_bounded_process(
        [sys.executable, "-u", "-c", "print('one'); print('two')"],
        timeout_s=5,
        output_callback=seen.append,
    )
    assert result.returncode == 0
    assert result.output == "one\ntwo\n"
    assert seen == ["one\n", "two\n"]


def test_timeout_kills_child_holding_stdout_pipe() -> None:
    script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        "print('ready', flush=True);time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(BoundedProcessTimeoutError) as caught:
        run_bounded_process(
            [sys.executable, "-u", "-c", script],
            timeout_s=0.2,
            term_grace_s=0.2,
            kill_grace_s=0.5,
            drain_grace_s=0.2,
        )
    assert time.monotonic() - started < 3
    assert "ready\n" in caught.value.result.output
    assert caught.value.result.remaining_pids_after_kill == []


def test_timeout_kills_child_that_escapes_process_group() -> None:
    script = (
        "import os,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import os,time;os.setsid();time.sleep(60)']);"
        "print(child.pid, flush=True);time.sleep(60)"
    )
    with pytest.raises(BoundedProcessTimeoutError) as caught:
        run_bounded_process(
            [sys.executable, "-u", "-c", script],
            timeout_s=0.2,
            term_grace_s=0.2,
            kill_grace_s=0.8,
            drain_grace_s=0.2,
        )
    escaped_pid = int(caught.value.result.output.strip())
    with pytest.raises(ProcessLookupError):
        os.kill(escaped_pid, 0)


def test_sigterm_ignoring_process_is_killed() -> None:
    script = (
        "import signal,time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "time.sleep(60)"
    )
    with pytest.raises(BoundedProcessTimeoutError) as caught:
        run_bounded_process(
            [sys.executable, "-c", script],
            timeout_s=0.2,
            term_grace_s=0.2,
            kill_grace_s=0.5,
            drain_grace_s=0.2,
        )
    assert caught.value.result.returncode == -signal.SIGKILL
    assert caught.value.result.remaining_pids_after_kill == []


def test_normal_parent_exit_does_not_leave_child_holding_pipe() -> None:
    script = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "print('parent-exit', flush=True)"
    )
    started = time.monotonic()
    result = run_bounded_process(
        [sys.executable, "-u", "-c", script],
        timeout_s=5,
        term_grace_s=0.2,
        kill_grace_s=0.5,
        drain_grace_s=0.2,
    )
    assert time.monotonic() - started < 3
    assert "parent-exit\n" in result.output
    assert result.remaining_pids_after_kill == []


def test_capture_limit_retains_output_tail() -> None:
    result = run_bounded_process(
        [
            sys.executable,
            "-c",
            "print('x' * 4096); print('PAYLOAD_AT_END')",
        ],
        timeout_s=5,
        max_capture_bytes=128,
    )
    assert result.output_truncated is True
    assert "PAYLOAD_AT_END" in result.output


def test_output_callback_failure_is_fail_closed() -> None:
    def _failed_callback(_line: str) -> None:
        raise RuntimeError("durable log write failed")

    with pytest.raises(BoundedProcessCleanupError) as caught:
        run_bounded_process(
            [sys.executable, "-c", "print('evidence')"],
            timeout_s=5,
            output_callback=_failed_callback,
        )
    assert any(
        "durable log write failed" in error
        for error in caught.value.result.inspection_errors
    )


def test_inspection_failure_is_fail_closed(monkeypatch) -> None:
    def _failed_inspection(_pgid: int, _timeout_s: float):
        raise RuntimeError("simulated ps failure")

    monkeypatch.setattr(
        bounded_process_runner,
        "_process_group_members",
        _failed_inspection,
    )
    with pytest.raises(BoundedProcessCleanupError) as caught:
        run_bounded_process(
            [sys.executable, "-c", "pass"],
            timeout_s=5,
        )
    assert any(
        "simulated ps failure" in error
        for error in caught.value.result.inspection_errors
    )


def test_timeout_remains_primary_when_inspection_also_fails(monkeypatch) -> None:
    def _failed_inspection(_pgid: int, _timeout_s: float):
        raise RuntimeError("simulated ps failure")

    monkeypatch.setattr(
        bounded_process_runner,
        "_process_group_members",
        _failed_inspection,
    )
    with pytest.raises(BoundedProcessTimeoutError) as caught:
        run_bounded_process(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            timeout_s=0.2,
            term_grace_s=0.2,
            kill_grace_s=0.5,
            drain_grace_s=0.2,
        )
    assert any(
        "simulated ps failure" in error
        for error in caught.value.result.inspection_errors
    )

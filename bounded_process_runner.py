"""Bounded subprocess execution with process-tree cleanup and streamed output."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Union


Command = Union[str, Sequence[str]]


@dataclass
class BoundedProcessResult:
    """Result and cleanup evidence for a bounded subprocess."""

    args: Command
    returncode: int
    output: str
    timed_out: bool
    output_truncated: bool
    process_token: str
    remaining_pids_after_term: List[int] = field(default_factory=list)
    remaining_pids_after_kill: List[int] = field(default_factory=list)
    inspection_errors: List[str] = field(default_factory=list)


class BoundedProcessError(RuntimeError):
    """Base error carrying the best available subprocess evidence."""

    def __init__(self, message: str, result: BoundedProcessResult) -> None:
        super().__init__(message)
        self.result = result


class BoundedProcessTimeoutError(BoundedProcessError):
    """The process exceeded its positive finite deadline."""


class BoundedProcessCleanupError(BoundedProcessError):
    """The process exited, but cleanup could not be proven complete."""


def _positive_finite_timeout(value: object, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{name} must be a positive finite number")
    return parsed


def _process_state(pid: int) -> Optional[str]:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (OSError, UnicodeError):
        return "?"
    command_end = stat_text.rfind(")")
    if command_end < 0:
        return "?"
    trailing = stat_text[command_end + 1 :].strip().split()
    return trailing[0] if trailing else "?"


def _token_processes(token: str) -> List[int]:
    """Find live descendants even if they escaped the original process group."""

    marker = f"RESOURCE_ADAPTIVE_PROCESS_TOKEN={token}".encode("utf-8")
    matches: List[int] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        raise RuntimeError("/proc is unavailable for process-tree verification")
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            environ = (entry / "environ").read_bytes()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        except OSError:
            continue
        if marker not in environ.split(b"\0"):
            continue
        state = _process_state(pid)
        if state is not None and state != "Z":
            matches.append(pid)
    return sorted(set(matches))


def _process_group_members(pgid: int, timeout_s: float) -> List[int]:
    """Return non-zombie members, failing closed when process inspection fails."""

    completed = subprocess.run(
        ["ps", "-eo", "pid=,pgid=,stat="],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_s,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "process inspection failed: "
            f"rc={completed.returncode}, stderr={(completed.stderr or '').strip()}"
        )
    members: List[int] = []
    for row in (completed.stdout or "").splitlines():
        fields = row.split()
        if len(fields) < 3:
            continue
        try:
            pid = int(fields[0])
            row_pgid = int(fields[1])
        except ValueError:
            continue
        state = fields[2]
        if row_pgid == pgid and pid != os.getpid() and not state.startswith("Z"):
            members.append(pid)
    return sorted(set(members))


def _live_owned_pids(
    pgid: int,
    token: str,
    inspection_timeout_s: float,
    inspection_errors: List[str],
) -> List[int]:
    pids = set()
    try:
        pids.update(_process_group_members(pgid, inspection_timeout_s))
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        inspection_errors.append(f"process_group_inspection: {exc}")
    try:
        pids.update(_token_processes(token))
    except RuntimeError as exc:
        inspection_errors.append(f"process_token_inspection: {exc}")
    pids.discard(os.getpid())
    return sorted(pids)


def _signal_owned_processes(pgid: int, pids: Sequence[int], sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass


def _wait_for_cleanup(
    pgid: int,
    token: str,
    deadline: float,
    inspection_timeout_s: float,
    inspection_errors: List[str],
) -> List[int]:
    remaining: List[int] = []
    while True:
        remaining = _live_owned_pids(
            pgid,
            token,
            inspection_timeout_s,
            inspection_errors,
        )
        if not remaining or time.monotonic() >= deadline:
            return remaining
        time.sleep(0.05)


def run_bounded_process(
    command: Command,
    *,
    timeout_s: float,
    shell: bool = False,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    output_callback: Optional[Callable[[str], None]] = None,
    term_grace_s: float = 10.0,
    kill_grace_s: float = 5.0,
    drain_grace_s: float = 2.0,
    inspection_timeout_s: float = 2.0,
    max_capture_bytes: int = 16 * 1024 * 1024,
) -> BoundedProcessResult:
    """Run a subprocess with a mandatory deadline and bounded cleanup.

    Output is consumed continuously so a verbose child cannot block on a full
    pipe.  A unique inherited token lets cleanup find children that create a
    new session and escape the original process group.
    """

    timeout = _positive_finite_timeout(timeout_s, "timeout_s")
    term_grace = _positive_finite_timeout(term_grace_s, "term_grace_s")
    kill_grace = _positive_finite_timeout(kill_grace_s, "kill_grace_s")
    drain_grace = _positive_finite_timeout(drain_grace_s, "drain_grace_s")
    inspection_timeout = _positive_finite_timeout(
        inspection_timeout_s,
        "inspection_timeout_s",
    )
    if isinstance(max_capture_bytes, bool) or int(max_capture_bytes) <= 0:
        raise ValueError("max_capture_bytes must be a positive integer")

    token = uuid.uuid4().hex
    child_env: Dict[str, str] = dict(os.environ)
    if env is not None:
        child_env.update({str(key): str(value) for key, value in env.items()})
    child_env["RESOURCE_ADAPTIVE_PROCESS_TOKEN"] = token

    proc = subprocess.Popen(
        command,
        shell=shell,
        cwd=cwd,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
        bufsize=1,
    )
    output_parts = deque()  # type: ignore[var-annotated]
    captured_bytes = 0
    output_truncated = False
    reader_errors: List[str] = []

    def _read_output() -> None:
        nonlocal captured_bytes, output_truncated
        assert proc.stdout is not None
        try:
            for line in iter(proc.stdout.readline, ""):
                if output_callback is not None:
                    output_callback(line)
                encoded_size = len(line.encode("utf-8", errors="replace"))
                if encoded_size >= int(max_capture_bytes):
                    retained = line.encode(
                        "utf-8",
                        errors="replace",
                    )[-int(max_capture_bytes) :].decode(
                        "utf-8",
                        errors="replace",
                    )
                    output_parts.clear()
                    output_parts.append(retained)
                    captured_bytes = len(
                        retained.encode("utf-8", errors="replace")
                    )
                    output_truncated = True
                    continue
                output_parts.append(line)
                captured_bytes += encoded_size
                while captured_bytes > int(max_capture_bytes) and output_parts:
                    removed = output_parts.popleft()
                    captured_bytes -= len(
                        removed.encode("utf-8", errors="replace")
                    )
                    output_truncated = True
        except BaseException as exc:
            # A callback is part of the evidence path (usually the durable
            # workload log).  If it fails, the command must not be reported as
            # a clean success merely because the child itself exited zero.
            reader_errors.append(
                f"output_reader: {type(exc).__name__}: {exc}"
            )

    reader = threading.Thread(
        target=_read_output,
        name=f"bounded-process-reader-{proc.pid}",
        daemon=True,
    )
    reader.start()
    timed_out = False
    inspection_errors: List[str] = []
    remaining_after_term: List[int] = []
    remaining_after_kill: List[int] = []

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        initial_pids = _live_owned_pids(
            proc.pid,
            token,
            inspection_timeout,
            inspection_errors,
        )
        _signal_owned_processes(proc.pid, initial_pids, signal.SIGTERM)
        remaining_after_term = _wait_for_cleanup(
            proc.pid,
            token,
            time.monotonic() + term_grace,
            inspection_timeout,
            inspection_errors,
        )
        if remaining_after_term:
            _signal_owned_processes(
                proc.pid,
                remaining_after_term,
                signal.SIGKILL,
            )
        remaining_after_kill = _wait_for_cleanup(
            proc.pid,
            token,
            time.monotonic() + kill_grace,
            inspection_timeout,
            inspection_errors,
        )
        try:
            proc.wait(timeout=kill_grace)
        except subprocess.TimeoutExpired:
            remaining_after_kill = sorted(
                set(remaining_after_kill)
                | set(
                    _live_owned_pids(
                        proc.pid,
                        token,
                        inspection_timeout,
                        inspection_errors,
                    )
                )
            )

    # A shell may exit while a descendant keeps the inherited stdout pipe
    # open.  Reap owned descendants before joining the reader so pipe draining
    # itself can never become the unbounded wait.
    residual_before_drain = _live_owned_pids(
        proc.pid,
        token,
        inspection_timeout,
        inspection_errors,
    )
    if residual_before_drain:
        _signal_owned_processes(
            proc.pid,
            residual_before_drain,
            signal.SIGTERM,
        )
        residual_before_drain = _wait_for_cleanup(
            proc.pid,
            token,
            time.monotonic() + term_grace,
            inspection_timeout,
            inspection_errors,
        )
        if residual_before_drain:
            _signal_owned_processes(
                proc.pid,
                residual_before_drain,
                signal.SIGKILL,
            )
            residual_before_drain = _wait_for_cleanup(
                proc.pid,
                token,
                time.monotonic() + kill_grace,
                inspection_timeout,
                inspection_errors,
            )
    remaining_after_kill = sorted(
        set(remaining_after_kill) | set(residual_before_drain)
    )

    reader.join(timeout=drain_grace)
    if reader.is_alive() and proc.stdout is not None:
        try:
            proc.stdout.close()
        except OSError:
            pass
        reader.join(timeout=drain_grace)
    if reader.is_alive():
        reader_errors.append("output reader did not stop within bounded drain grace")

    residual = _live_owned_pids(
        proc.pid,
        token,
        inspection_timeout,
        inspection_errors,
    )
    if residual:
        _signal_owned_processes(proc.pid, residual, signal.SIGKILL)
        residual = _wait_for_cleanup(
            proc.pid,
            token,
            time.monotonic() + kill_grace,
            inspection_timeout,
            inspection_errors,
        )
    remaining_after_kill = sorted(set(remaining_after_kill) | set(residual))
    inspection_errors.extend(reader_errors)
    result = BoundedProcessResult(
        args=command,
        returncode=int(proc.returncode if proc.returncode is not None else -signal.SIGKILL),
        output="".join(output_parts),
        timed_out=timed_out,
        output_truncated=output_truncated,
        process_token=token,
        remaining_pids_after_term=remaining_after_term,
        remaining_pids_after_kill=remaining_after_kill,
        inspection_errors=inspection_errors,
    )
    if timed_out:
        raise BoundedProcessTimeoutError(
            f"process timed out after {timeout:.3f} seconds",
            result,
        )
    if remaining_after_kill or inspection_errors:
        raise BoundedProcessCleanupError(
            "process cleanup could not be proven complete",
            result,
        )
    return result

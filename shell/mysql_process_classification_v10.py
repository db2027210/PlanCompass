"""Strict shared classification for local MySQL Algorithm C process evidence."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePath
import re
import shlex
import stat
from typing import Optional, Sequence


_DEVICE_OBSERVER_SCRIPTS = frozenset({"host_background_sampler.py"})
_EXACT_OBSERVER_SCRIPTS = frozenset({"monitor_pg5_five_experiments.py"})
_CONTROLLER_SCRIPTS = frozenset({
    "debug_mysql_gradient_round.py",
    "formal_mysql_sgd_campaign.py",
    "job_local_readiness_evidence.py",
    "mysql_four_scene_shared_snapshot.py",
    "mysql_host_process_device_authority.py",
    "mysql_parameter_optimizer.py",
    "mysql_tpcc_capture_evidence_collector.py",
    "mysql_xp_local_capture_entry.py",
    "mysql_xp_capture_evidence_collector.py",
    "parameter_optimizer.py",
    "run_tpcc_sensitive_stable_point.py",
    "tpcc_local_binding_capture_v2.py",
})
_BENCHMARK_MARKERS = (
    "hybench", "benchbase", "dbworkload", "formal_job", "formal_tpch",
    "pgbench",
)
_BENCHMARK_SCRIPTS = frozenset({
    "benchmark_monitor.py",
    "run_tpcc_sensitive_mix.py",
    "run_tpcc_sensitive_mix.sh",
})
_TPCC_MONITOR = "/tmp/monitor_tpcc_attempt12_300s.sh"
_DEVICE = re.compile(r"^(?:nvme\d+n\d+(?:p\d+)?|sd[a-z]+\d*|vd[a-z]+\d*)$")
_PG5_INLINE_PROBE_EXECUTABLE = "/home/anonymous/miniconda3/bin/python3"
_PG5_INLINE_PROBE_ROOT = (
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes"
)
_PG5_INLINE_PROBE_SHA256 = (
    "f0409a4a1fbbb76be5fdb0a0445ff329f971676ea0fdade8518cff6cc111118f"
)
_BENCHMARK_MONITOR_SCRIPT = (
    "/home/anonymous/program/resource_adaptive/shell/benchmark_monitor.py"
)
_BENCHMARK_MONITOR_SHA256 = (
    "dd6c35407c9f585ffc9f3452e7ebca854a01f15aeaac5c22206825637efa7019"
)
_BENCHMARK_MONITOR_READ_ONLY_PS = (
    "ps aux | grep -E 'bash.*launch_hybench_ap_attempt' | grep -v grep"
)


def _absolute_python_script(
    words: list[str], names: frozenset[str]
) -> str | None:
    if not words or not PurePath(words[0]).name.startswith("python"):
        return None
    matches = [word for word in words[1:] if PurePath(word).name in names]
    if len(matches) != 1:
        return None
    script = PurePath(matches[0])
    if not script.is_absolute() or matches[0] != str(script):
        return None
    return matches[0]


def _is_known_observer(words: list[str]) -> bool:
    script = _absolute_python_script(words, _DEVICE_OBSERVER_SCRIPTS)
    if script is None:
        return False
    try:
        device_index = words.index("--device", words.index(script) + 1)
        device = words[device_index + 1]
    except (ValueError, IndexError):
        return False
    return _DEVICE.fullmatch(device) is not None


def _is_exact_observer(words: list[str]) -> bool:
    return _absolute_python_script(words, _EXACT_OBSERVER_SCRIPTS) is not None


def _is_exact_pg5_inline_probe(words: Sequence[str]) -> bool:
    """Recognize only the frozen read-only child of the PG status watcher."""
    if (
        len(words) != 6
        or words[0] != _PG5_INLINE_PROBE_EXECUTABLE
        or words[1] != "-c"
        or hashlib.sha256(words[2].encode("utf-8")).hexdigest()
        != _PG5_INLINE_PROBE_SHA256
    ):
        return False
    root = PurePath(words[3])
    if (
        not root.is_absolute()
        or str(root) == _PG5_INLINE_PROBE_ROOT
        or not str(root).startswith(_PG5_INLINE_PROBE_ROOT + "/")
    ):
        return False
    return bool(words[4]) and words[5] in {"cpu", "memory", "io", ""}


def _is_exact_benchmark_monitor_probe(words: Sequence[str]) -> bool:
    """Recognize only benchmark_monitor's frozen read-only shell probe."""
    return tuple(words) == (
        "/bin/sh",
        "-c",
        _BENCHMARK_MONITOR_READ_ONLY_PS,
    )


def _is_exact_benchmark_monitor_parent(
    words: Sequence[str], *, cwd: str | Path | None
) -> bool:
    """Recognize the parent by resolved path, regular-file type and hash."""
    if len(words) not in {2, 3}:
        return False
    if PurePath(words[0]).name not in {"python", "python3"}:
        return False
    script_index = 1
    if len(words) == 3:
        if words[1] != "-u":
            return False
        script_index = 2
    script_arg = Path(words[script_index])
    if script_arg.is_absolute():
        candidate = script_arg
    else:
        if cwd is None:
            return False
        cwd_path = Path(cwd)
        if not cwd_path.is_absolute():
            return False
        candidate = cwd_path / script_arg
    try:
        if candidate.is_symlink():
            return False
        if (
            candidate.resolve(strict=True)
            != Path(_BENCHMARK_MONITOR_SCRIPT)
        ):
            return False
        descriptor = os.open(
            candidate,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        return False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return False
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest() == _BENCHMARK_MONITOR_SHA256
    finally:
        os.close(descriptor)


def _is_exact_tpcc_monitor(words: list[str]) -> bool:
    return (
        len(words) == 2
        and PurePath(words[0]).name in {"bash", "sh"}
        and words[1] == _TPCC_MONITOR
        and PurePath(words[1]).is_absolute()
    )


def _is_exact_python_controller(words: list[str]) -> bool:
    return _absolute_python_script(words, _CONTROLLER_SCRIPTS) is not None


def _is_database_process(words: list[str], engine: str) -> bool:
    if not words:
        return False
    executable = PurePath(words[0]).name.lower()
    if engine == "mysql":
        return executable == "mysqld"
    return (
        executable in {"postgres", "postmaster"}
        or executable.startswith("postgres:")
    )


def _is_benchmark_process(words: list[str]) -> bool:
    if not words:
        return False
    executable = PurePath(words[0]).name.lower()
    if executable in _BENCHMARK_SCRIPTS or executable == "pgbench":
        return True
    if any(PurePath(word).name in _BENCHMARK_SCRIPTS for word in words[1:]):
        return True
    if not (
        executable == "java"
        or executable.startswith("python")
        or executable in {"bash", "sh"}
        or any(marker in executable for marker in _BENCHMARK_MARKERS)
    ):
        return False
    lowered_words = " ".join(words).lower()
    return any(marker in lowered_words for marker in _BENCHMARK_MARKERS)


def classify_process(
    command: str,
    *,
    argv: Sequence[str] | None = None,
    cwd: str | Path | None = None,
) -> Optional[str]:
    """Classify one cmdline; exact observer signatures take precedence.

    ``argv`` is required for an inline ``python -c`` observer because joining
    ``/proc/<pid>/cmdline`` on spaces destroys the source-code argument
    boundary. Callers without the original argv remain fail-closed.
    """
    if argv is None:
        try:
            words = shlex.split(command)
        except ValueError:
            words = []
    else:
        words = list(argv)
    if _is_exact_pg5_inline_probe(words):
        return "observer"
    if (
        _is_exact_benchmark_monitor_probe(words)
        or _is_exact_benchmark_monitor_parent(words, cwd=cwd)
    ):
        return "observer"
    if _is_known_observer(words) or _is_exact_observer(words):
        return "observer"
    if _is_exact_tpcc_monitor(words):
        return "observer"
    if _is_exact_python_controller(words):
        return "controller"
    lowered = command.lower()
    if "monitor_tpcc_attempt12_300s.sh" in lowered:
        return "unknown"
    if _is_benchmark_process(words):
        return "benchmark"
    if _is_database_process(words, "mysql"):
        return "mysql"
    if _is_database_process(words, "postgresql"):
        return "postgresql"
    return None

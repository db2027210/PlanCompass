#!/usr/bin/env python3
"""Local enforcement tests for the shared host02 JOB/XP pressure mutex."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host02_job_xp_mutex import (  # noqa: E402
    Host02IsolatedParallelGuard,
    Host02JobXpMutex,
    Host02MutexError,
    mutex_from_campaign,
)


def _root():
    runtime = ROOT / "runtime_status"
    runtime.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="host02_mutex_test_", dir=runtime)


def _probe(scenario: str, phase: str):
    return {
        "observed_at": "2026-07-23T00:00:00+08:00",
        "probe_id": f"local-{scenario}-{phase}",
        "job_pressure_count": 0,
        "xp_pressure_count": 0,
    }


def _parallel_probe(scenario: str, phase: str):
    return {
        **_probe(scenario, phase),
        "job_pressure_count": 1 if scenario == "hybench_xp_io" else 0,
        "xp_pressure_count": 1 if scenario == "job_memory" else 0,
        "isolation_verified": True,
        "cross_engine_comparison_prohibited": True,
    }


def _paths(raw: str) -> tuple[Path, Path]:
    root = Path(raw).resolve()
    return root / "job_xp_pressure.lock", root / "job_xp_lease_journal.jsonl"


def _events(journal: Path) -> list[dict]:
    return [json.loads(line) for line in journal.read_text().splitlines() if line]


def test_non_tmp_absolute_control_paths_are_mandatory() -> None:
    try:
        Host02JobXpMutex(
            scenario="job_memory",
            pressure_probe=_probe,
            lock_path=Path("/tmp/forbidden.lock"),
            journal_path=Path("/tmp/forbidden.jsonl"),
        )
    except Host02MutexError as exc:
        assert "must not be under /tmp" in str(exc)
    else:
        raise AssertionError("/tmp mutex path must fail closed")


def test_full_scope_and_atomic_handoff_journal() -> None:
    phases = []

    def probe(scenario: str, phase: str):
        phases.append((scenario, phase))
        return _probe(scenario, phase)

    with _root() as raw:
        lock, journal = _paths(raw)
        with Host02JobXpMutex(
            scenario="job_memory", pressure_probe=probe,
            lock_path=lock, journal_path=journal,
        ):
            assert phases == [("job_memory", "before_pressure")]
        assert phases[-1] == ("job_memory", "after_recovery_checksum")
        rows = _events(journal)
        names = [row["event"] for row in rows]
        assert names == [
            "lock_acquired",
            "preflight_zero_pressure_passed",
            "handoff_acquired",
            "recovery_checksum_scope_complete",
            "lock_released",
        ]
        assert rows[2]["payload"]["atomic_under_flock"] is True
        assert rows[2]["payload"]["pressure_scope"] == (
            "before_R0_through_recovery_and_checksum"
        )
        assert rows[3]["payload"]["pressure_scope_complete"] is True
        state = json.loads(journal.with_suffix(".state.json").read_text())
        assert state["event"] == "lock_released"
        assert state["event_sha256"] == rows[-1]["event_sha256"]


def test_campaign_factory_accepts_isolated_parallel_mode() -> None:
    with _root() as raw:
        lock, journal = _paths(raw)
        config = {
            "host02_job_xp_mutex": {
                "required": True,
                "scenario": "job_memory",
                "mode": "isolated_parallel_dynamic_gate",
                "lock_path": str(lock),
                "journal_path": str(journal),
            }
        }
        guard = mutex_from_campaign(
            config,
            {"host02_pressure_probe": _parallel_probe},
            "job",
        )
        assert isinstance(guard, Host02IsolatedParallelGuard)
        with guard:
            pass


def test_second_holder_is_rejected_for_job_and_xp() -> None:
    with _root() as raw:
        lock, journal = _paths(raw)
        with Host02JobXpMutex(
            scenario="job_memory", pressure_probe=_probe,
            lock_path=lock, journal_path=journal,
        ):
            code = """
from pathlib import Path
from host02_job_xp_mutex import Host02JobXpMutex, Host02MutexError
def probe(scenario, phase):
    return {'observed_at':'x','probe_id':'x','job_pressure_count':0,'xp_pressure_count':0}
try:
    with Host02JobXpMutex(scenario='hybench_xp_io', pressure_probe=probe, lock_path=Path(sys.argv[1]), journal_path=Path(sys.argv[2])):
        pass
except Host02MutexError:
    raise SystemExit(23)
raise SystemExit(0)
"""
            completed = subprocess.run(
                [sys.executable, "-c", "import sys\n" + code, str(lock), str(journal)],
                cwd=ROOT, check=False,
            )
            assert completed.returncode == 23


def test_kernel_releases_crashed_owner_and_next_holder_audits_stale_state() -> None:
    with _root() as raw:
        lock, journal = _paths(raw)
        crash = """
import os, sys
from pathlib import Path
from host02_job_xp_mutex import Host02JobXpMutex
def probe(scenario, phase):
    return {'observed_at':'x','probe_id':'x','job_pressure_count':0,'xp_pressure_count':0}
mutex = Host02JobXpMutex(scenario='job_memory', pressure_probe=probe, lock_path=Path(sys.argv[1]), journal_path=Path(sys.argv[2]), owner_id='crashed-owner')
mutex.__enter__()
os._exit(17)
"""
        completed = subprocess.run(
            [sys.executable, "-c", crash, str(lock), str(journal)],
            cwd=ROOT, check=False,
        )
        assert completed.returncode == 17
        with Host02JobXpMutex(
            scenario="hybench_xp_io", pressure_probe=_probe,
            lock_path=lock, journal_path=journal, owner_id="recovery-owner",
        ):
            pass
        rows = _events(journal)
        stale = [row for row in rows if row["event"] == "stale_owner_recovered"]
        assert len(stale) == 1
        assert stale[0]["payload"]["stale_owner_id"] == "crashed-owner"
        assert stale[0]["payload"]["kernel_lock_reacquired"] is True


def test_zero_pressure_failure_rejects_before_pressure() -> None:
    def busy(scenario: str, phase: str):
        result = _probe(scenario, phase)
        result["xp_pressure_count"] = 1
        return result

    with _root() as raw:
        lock, journal = _paths(raw)
        try:
            with Host02JobXpMutex(
                scenario="job_memory", pressure_probe=busy,
                lock_path=lock, journal_path=journal,
            ):
                raise AssertionError("pressure scope must not start")
        except Host02MutexError as exc:
            assert "zero-pressure gate failed" in str(exc)
        else:
            raise AssertionError("busy peer must fail closed")
        assert [row["event"] for row in _events(journal)] == [
            "lock_acquired", "acquisition_rejected"
        ]


def test_aborted_campaign_checks_zero_pressure_before_unlock() -> None:
    phases = []

    def probe(scenario: str, phase: str):
        phases.append(phase)
        return _probe(scenario, phase)

    with _root() as raw:
        lock, journal = _paths(raw)
        try:
            with Host02JobXpMutex(
                scenario="job_memory", pressure_probe=probe,
                lock_path=lock, journal_path=journal,
            ):
                raise ValueError("injected campaign failure")
        except ValueError as exc:
            assert str(exc) == "injected campaign failure"
        else:
            raise AssertionError("original campaign failure must propagate")
        assert phases == ["before_pressure", "after_recovery_checksum"]
        names = [row["event"] for row in _events(journal)]
        assert names[-3:] == [
            "campaign_aborted", "aborted_scope_zero_pressure_passed", "lock_released"
        ]


def test_both_production_launch_paths_resolve_the_same_lock() -> None:
    with _root() as raw:
        lock, journal = _paths(raw)
        bundle = {"host02_pressure_probe": _probe}
        common = {
            "required": True,
            "lock_path": str(lock),
            "journal_path": str(journal),
        }
        job = mutex_from_campaign(
            {"host02_job_xp_mutex": {**common, "scenario": "job_memory"}},
            bundle, "job",
        )
        xp = mutex_from_campaign(
            {"host02_job_xp_mutex": {**common, "scenario": "hybench_xp_io"}},
            bundle, "hybench_xp",
        )
        assert job is not None and xp is not None
        assert job.lock_path == xp.lock_path == lock
        assert job.journal_path == xp.journal_path == journal


if __name__ == "__main__":
    test_non_tmp_absolute_control_paths_are_mandatory()
    test_full_scope_and_atomic_handoff_journal()
    test_second_holder_is_rejected_for_job_and_xp()
    test_kernel_releases_crashed_owner_and_next_holder_audits_stale_state()
    test_zero_pressure_failure_rejects_before_pressure()
    test_aborted_campaign_checks_zero_pressure_before_unlock()
    test_both_production_launch_paths_resolve_the_same_lock()
    print("host02 JOB/XP mutex tests passed")

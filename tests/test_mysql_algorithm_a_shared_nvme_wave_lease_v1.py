from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts import mysql_algorithm_a_shared_nvme_wave_lease_v1 as lease


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_xp_measurement_is_wrapped_with_released_lease(tmp_path: Path) -> None:
    state_path = tmp_path / "algorithm_a_state.json"
    _write(state_path, {"status": "RUNNING", "stage": "START"})
    calls = []

    def original(**kwargs):
        calls.append(kwargs)
        return {"xp_cpu_w1": {"hard_gate_passed": True}}, {"status": "RUNNING"}

    def load(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def atomic(path: Path, value: dict):
        _write(path, value)

    base = SimpleNamespace(
        measure=original,
        alg=SimpleNamespace(load=load),
        atomic=atomic,
    )
    lease.install(base)
    result = base.measure(
        workers={"xp_cpu_w1": {"scene": "hybench_xp_cpu"}},
        state_path=state_path,
        state={"status": "RUNNING"},
        stage_prefix="FORMAL_BASELINE",
        run_id="fresh_xp_cpu",
    )
    assert result[0]["xp_cpu_w1"]["hard_gate_passed"] is True
    assert len(calls) == 1
    evidence = tmp_path / "shared_storage_leases/FORMAL_BASELINE.json"
    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["status"] == "RELEASED"
    assert document["lease_covers_reset_and_measurement"] is True
    assert document["worker_ids"] == ["xp_cpu_w1"]


def test_non_xp_measurement_is_not_wrapped(tmp_path: Path) -> None:
    called = []

    def original(**kwargs):
        called.append(kwargs)
        return "plain"

    base = SimpleNamespace(measure=original)
    lease.install(base)
    result = base.measure(workers={"job_w1": {"scene": "job_memory"}})
    assert result == "plain"
    assert len(called) == 1

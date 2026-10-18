from __future__ import annotations

from pathlib import Path

import pytest

from scripts import run_mysql_four_scene_algorithm_c_v2 as controller


ACTIVE = ["io_block_read_cost", "row_evaluate_cost"]


def ranges(ratio: float) -> dict:
    return {
        "io_block_read_cost": {"lower": 1.0, "upper": ratio},
        "row_evaluate_cost": {"lower": 0.1, "upper": 0.1 * ratio},
    }


def params(io: float = 1.005, row: float = 0.1005) -> dict:
    return {"io_block_read_cost": io, "row_evaluate_cost": row}


def test_formal_outer_attempt_limit_is_ten() -> None:
    assert controller.MAXIMUM_OUTER_ATTEMPTS == 10


def test_exactly_one_percent_interval_is_ready() -> None:
    audit = controller.interval_convergence_audit(ranges(1.01), ACTIVE)
    assert audit["all_active_parameters_within_threshold"] is True
    assert audit["max_relative_width"] == pytest.approx(0.01)


def test_interval_over_one_percent_is_not_ready() -> None:
    audit = controller.interval_convergence_audit(ranges(1.0100001), ACTIVE)
    assert audit["all_active_parameters_within_threshold"] is False


def test_two_consecutive_accepted_optima_in_same_interval_converge() -> None:
    first = controller.advance_interval_convergence(
        ranges=ranges(1.01), active=ACTIVE, candidate_params=params(),
        accepted=True, previous_stable_rounds=0, previous_interval=None,
    )
    assert first["stable_rounds"] == 1
    assert first["converged"] is False
    second = controller.advance_interval_convergence(
        ranges=ranges(1.005), active=ACTIVE, candidate_params=params(1.004, 0.1004),
        accepted=True, previous_stable_rounds=first["stable_rounds"],
        previous_interval=first["stable_interval"],
    )
    assert second["candidate_inside_previous_interval"] is True
    assert second["stable_rounds"] == 2
    assert second["converged"] is True


def test_rejection_resets_consecutive_stability() -> None:
    first = controller.advance_interval_convergence(
        ranges=ranges(1.01), active=ACTIVE, candidate_params=params(),
        accepted=True, previous_stable_rounds=0, previous_interval=None,
    )
    rejected = controller.advance_interval_convergence(
        ranges=ranges(1.01), active=ACTIVE, candidate_params=None,
        accepted=False, previous_stable_rounds=first["stable_rounds"],
        previous_interval=first["stable_interval"],
    )
    assert rejected["stable_rounds"] == 0
    assert rejected["stable_interval"] is None
    assert rejected["converged"] is False


def test_candidate_outside_saved_interval_restarts_stability_at_one() -> None:
    prior = {
        "io_block_read_cost": {"lower": 1.0, "upper": 1.01},
        "row_evaluate_cost": {"lower": 0.1, "upper": 0.101},
    }
    observed = controller.advance_interval_convergence(
        ranges=ranges(1.005), active=ACTIVE,
        candidate_params=params(1.02, 0.1004), accepted=True,
        previous_stable_rounds=1, previous_interval=prior,
    )
    assert observed["candidate_inside_previous_interval"] is False
    assert observed["stable_rounds"] == 1
    assert observed["converged"] is False


def test_serialized_candidate_on_saved_boundary_preserves_stability() -> None:
    """The 12-significant-digit candidate emitted by Outer 18 is the boundary."""
    active = ["disk_temptable_row_cost", "memory_block_read_cost"]
    prior = {
        "disk_temptable_row_cost": {
            "lower": 1.0221552870253252,
            "upper": 1.0267625132285731,
        },
        "memory_block_read_cost": {
            "lower": 0.24444118682917723,
            "upper": 0.24554297229700944,
        },
    }
    observed = controller.advance_interval_convergence(
        ranges={
            "disk_temptable_row_cost": {
                "lower": 1.024456310154804,
                "upper": 1.0267625132285731,
            },
            "memory_block_read_cost": {
                "lower": 0.24444118682917723,
                "upper": 0.24499146018962534,
            },
        },
        active=active,
        candidate_params={
            # 1.4268586312482512e-12 above the saved upper boundary.
            "disk_temptable_row_cost": 1.02676251323,
            # 1.7721935030579061e-13 below the saved lower boundary.
            "memory_block_read_cost": 0.244441186829,
        },
        accepted=True,
        previous_stable_rounds=1,
        previous_interval=prior,
    )
    assert observed["candidate_inside_previous_interval"] is True
    assert observed["stable_rounds"] == 2
    assert observed["converged"] is True
    assert observed["boundary_membership_rel_tolerance"] == 2e-12
    assert observed["boundary_membership_abs_tolerance"] == 2e-12


def test_material_boundary_violation_is_not_hidden_by_rounding_tolerance() -> None:
    prior = {
        "io_block_read_cost": {"lower": 1.0, "upper": 1.01},
        "row_evaluate_cost": {"lower": 0.1, "upper": 0.101},
    }
    observed = controller.advance_interval_convergence(
        ranges=ranges(1.005), active=ACTIVE,
        candidate_params=params(0.99999999, 0.1004), accepted=True,
        previous_stable_rounds=1, previous_interval=prior,
    )
    assert observed["candidate_inside_previous_interval"] is False
    assert observed["stable_rounds"] == 1
    assert observed["converged"] is False


def test_xp_session_drain_waits_for_transient_connections(monkeypatch) -> None:
    observed = iter((1, 0))
    monkeypatch.setattr(
        controller,
        "xp_active_client_session_count",
        lambda worker, **kwargs: (
            next(observed) if worker["worker_id"] == "xp_cpu_w1" else 0
        ),
    )
    monkeypatch.setattr(controller.time, "sleep", lambda seconds: None)
    workers = {worker_id: {"worker_id": worker_id} for worker_id in controller.XP_IDS}
    result = controller.wait_for_xp_session_drain(
        workers, timeout_seconds=1.0, poll_seconds=0.01,
    )
    assert result["status"] == "PASS"
    assert len(result["polls"]) == 2
    assert result["polls"][0]["active_sessions"]["xp_cpu_w1"] == 1
    assert result["polls"][1]["active_sessions"]["xp_cpu_w1"] == 0


def test_xp_session_drain_retries_transient_probe_timeout(monkeypatch) -> None:
    attempts = {worker_id: 0 for worker_id in controller.XP_IDS}

    def probe(worker, **kwargs):
        worker_id = worker["worker_id"]
        attempts[worker_id] += 1
        if worker_id == "xp_io_w1" and attempts[worker_id] == 1:
            raise controller.subprocess.TimeoutExpired("mysql", kwargs["timeout_seconds"])
        return 0

    monkeypatch.setattr(controller, "xp_active_client_session_count", probe)
    monkeypatch.setattr(controller.time, "sleep", lambda seconds: None)
    workers = {worker_id: {"worker_id": worker_id} for worker_id in controller.XP_IDS}
    result = controller.wait_for_xp_session_drain(
        workers, timeout_seconds=1.0, poll_seconds=0.01,
    )
    assert result["status"] == "PASS"
    assert result["probe_concurrency"] == 4
    assert result["all_workers_explicit_zero_required"] is True
    assert result["polls"][0]["active_sessions"]["xp_io_w1"] is None
    assert result["polls"][0]["probe_errors"]["xp_io_w1"]["type"] == "TimeoutExpired"
    assert result["polls"][1]["active_sessions"]["xp_io_w1"] == 0


def test_exact_zombie_xp_container_is_proven_unresponsive(monkeypatch) -> None:
    worker_id = "xp_cpu_w1"
    container = controller.reset.XP_CONTAINER_ALLOWLIST[worker_id]
    container_id = "a" * 64
    inspect = {
        "Id": container_id,
        "State": {"Running": True},
        "Config": {"Labels": {
            "ra.owner": "mysql_four_scene_v5", "ra.worker_id": worker_id,
        }},
    }
    monkeypatch.setattr(controller.reset, "_docker_json", lambda *args: inspect)
    monkeypatch.setattr(
        controller.reset,
        "_docker_run",
        lambda *args, **kwargs: controller.subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="PID STAT COMMAND COMMAND\n123 Z mysqld /usr/sbin/mysqld\n",
        ),
    )
    result = controller.classify_xp_mysql_liveness({
        "worker_id": worker_id, "container": container,
        "container_id": container_id,
    })
    assert result["status"] == "PROVEN_NO_LIVE_MYSQLD"
    assert result["safe_to_discard_unresponsive_predecessor"] is True
    assert result["live_mysqld_rows"] == []


@pytest.mark.parametrize("state", ["S", "D"])
def test_live_or_blocked_mysqld_is_never_discard_exempt(monkeypatch, state) -> None:
    worker_id = "xp_io_w1"
    container = controller.reset.XP_CONTAINER_ALLOWLIST[worker_id]
    container_id = "b" * 64
    inspect = {
        "Id": container_id,
        "State": {"Running": True},
        "Config": {"Labels": {
            "ra.owner": "mysql_four_scene_v5", "ra.worker_id": worker_id,
        }},
    }
    monkeypatch.setattr(controller.reset, "_docker_json", lambda *args: inspect)
    monkeypatch.setattr(
        controller.reset,
        "_docker_run",
        lambda *args, **kwargs: controller.subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=f"PID STAT COMMAND COMMAND\n456 {state} mysqld /usr/sbin/mysqld\n",
        ),
    )
    result = controller.classify_xp_mysql_liveness({
        "worker_id": worker_id, "container": container,
        "container_id": container_id,
    })
    assert result["status"] == "LIVE_OR_UNPROVEN"
    assert result["safe_to_discard_unresponsive_predecessor"] is False


def test_mismatched_xp_container_is_never_discard_exempt() -> None:
    result = controller.classify_xp_mysql_liveness({
        "worker_id": "xp_cpu_w1", "container": "do_not_touch_0708",
        "container_id": "c" * 64,
    })
    assert result["status"] == "IDENTITY_MISMATCH"
    assert result["safe_to_discard_unresponsive_predecessor"] is False


def test_session_drain_skips_only_proven_dead_mysqld(monkeypatch) -> None:
    probed = []

    def probe(worker, **kwargs):
        probed.append(worker["worker_id"])
        return 0

    monkeypatch.setattr(controller, "xp_active_client_session_count", probe)
    workers = {worker_id: {"worker_id": worker_id} for worker_id in controller.XP_IDS}
    dead = "xp_cpu_w1"
    result = controller.wait_for_xp_session_drain(
        workers,
        liveness={dead: {
            "status": "PROVEN_NO_LIVE_MYSQLD",
            "safe_to_discard_unresponsive_predecessor": True,
        }},
        timeout_seconds=1.0, poll_seconds=0.01,
    )
    assert dead not in probed
    assert result["provably_no_live_mysqld_workers"] == [dead]
    assert result["polls"][0]["active_sessions"][dead] == 0
    assert result["all_live_workers_explicit_zero_required"] is True


def test_reset_passes_unresponsive_flag_only_for_proven_dead_worker(
    monkeypatch, tmp_path,
) -> None:
    dead = "xp_cpu_w1"
    workers = {
        worker_id: {
            "worker_id": worker_id,
            "container": controller.reset.XP_CONTAINER_ALLOWLIST[worker_id],
        }
        for worker_id in controller.XP_IDS
    }
    frozen_workers = [dict(row) for row in workers.values()]
    freeze = {
        "storage": {"runtime_root": str(tmp_path / "runtime")},
        "workers": frozen_workers,
    }
    monkeypatch.setattr(
        controller, "classify_xp_mysql_liveness",
        lambda worker: {
            "status": (
                "PROVEN_NO_LIVE_MYSQLD" if worker["worker_id"] == dead
                else "LIVE_OR_UNPROVEN"
            ),
            "safe_to_discard_unresponsive_predecessor": worker["worker_id"] == dead,
        },
    )
    monkeypatch.setattr(
        controller, "wait_for_xp_session_drain",
        lambda *args, **kwargs: {"status": "PASS", "liveness": kwargs["liveness"]},
    )
    observed = {}

    def prepare(*args, **kwargs):
        worker = args[1]
        observed[worker["worker_id"]] = kwargs["allow_unresponsive_old_container"]
        return dict(worker)

    monkeypatch.setattr(controller.reset, "_prepare_fresh_xp", prepare)
    monkeypatch.setattr(
        controller.users, "_run_worker",
        lambda worker, *args, **kwargs: dict(worker),
    )
    monkeypatch.setattr(
        controller.reset, "purge_completed_discard_quarantine",
        lambda **kwargs: {},
    )
    controller.reset_xp(
        freeze, workers, tmp_path / "run", "batch", worker_ids={dead},
    )
    assert observed == {dead: True}


def test_measurement_reset_evidence_is_scoped_to_current_run() -> None:
    run_root = Path("/history/run_001")
    assert controller.measurement_campaign_root(
        run_root / "formal_incumbent_baseline"
    ) == run_root
    assert controller.measurement_campaign_root(
        run_root / "formal_baseline"
    ) == run_root
    assert controller.measurement_campaign_root(
        run_root / "outer_005" / "top5_batch_1"
    ) == run_root

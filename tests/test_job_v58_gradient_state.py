from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
ROBUST = TOOLS / "run_job_round8_strict_serial_robust_v11_gradient_state_guard.py"
FORMAL = TOOLS / "job_formal_target10_batch_controller_v2_gradient_commit.py"
REPAIR = TOOLS / "repair_job_v57_to_v58_gradient_state.py"
sys.path.insert(0, str(TOOLS))

import repair_job_v57_to_v58_gradient_state as repair


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state(accepted: int, score: float, param: float, gradient: int) -> dict:
    return {
        "accepted_count": accepted,
        "incumbent_score": score,
        "incumbent_params": {"p": param},
        "range_by_parameter": {"p": {"min": 1.0, "max": param}},
        "gradient_observation_count": gradient,
    }


def load_robust() -> ModuleType:
    fake = ModuleType("run_job_round8_strict_serial_robust_v10_resource_median")
    def stripped_v10_decision(candidate, actual, current, **kwargs):
        del actual, kwargs
        fields = (
            "accepted_count",
            "incumbent_score",
            "incumbent_params",
            "range_by_parameter",
        )
        before = {name: copy.deepcopy(current[name]) for name in fields}
        accepted = bool(candidate.get("accept"))
        if accepted:
            current["accepted_count"] += 1
            current["incumbent_score"] = 2.0
            current["incumbent_params"] = {"p": 2.0}
            current["range_by_parameter"] = {"p": {"min": 1.0, "max": 2.0}}
            current["gradient_observation_count"] += 1
        after = {name: copy.deepcopy(current[name]) for name in fields}
        return {
            "accepted": accepted,
            "gradient_update_applied": accepted,
            "incumbent_update_applied": accepted,
            "range_update_applied": accepted,
            "incumbent_state_before": before,
            "incumbent_state_after": after,
        }

    fake.decide_state_update = stripped_v10_decision
    fake.base = SimpleNamespace()
    fake.robust_v7 = SimpleNamespace()
    previous = sys.modules.get(fake.__name__)
    sys.modules[fake.__name__] = fake
    try:
        spec = importlib.util.spec_from_file_location("job_v58_robust_test", ROBUST)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(fake.__name__, None)
        else:
            sys.modules[fake.__name__] = previous
    return module


def transition_result() -> tuple[dict, dict]:
    initial = state(0, 1.0, 1.0, 0)
    accepted_after = state(1, 2.0, 2.0, 1)
    rows = [
        {
            "rank": 1,
            "selection_role": "trusted",
            "technical_failure": False,
            "accepted": True,
            "gradient_update_applied": True,
            "incumbent_update_applied": True,
            "range_update_applied": True,
            "candidate_binding": {"candidate_params": {"p": 2.0}},
            "incumbent_state_before": copy.deepcopy(initial),
            "incumbent_state_after": copy.deepcopy(accepted_after),
        },
        {
            "rank": 2,
            "selection_role": "trusted",
            "technical_failure": False,
            "accepted": False,
            "gradient_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "incumbent_state_before": copy.deepcopy(accepted_after),
            "incumbent_state_after": copy.deepcopy(accepted_after),
        },
        {
            "rank": 4,
            "selection_role": "probe",
            "technical_failure": False,
            "accepted": False,
            "gradient_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "incumbent_state_before": copy.deepcopy(accepted_after),
            "incumbent_state_after": copy.deepcopy(accepted_after),
        },
    ]
    failure = {
        "rank": 3,
        "technical_failure": True,
        "accepted": False,
        "gradient_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
    }
    return initial, {
        "successful": rows,
        "technical_failures": [failure],
        "successful_real_replay_count": 3,
        "technical_failure_count": 1,
        "final_state": copy.deepcopy(accepted_after),
    }


def expect_runtime(callable_value, fragment: str) -> None:
    try:
        callable_value()
    except RuntimeError as error:
        assert fragment in str(error), str(error)
        return
    raise AssertionError("invalid transition unexpectedly passed")


def test_accepted_rejected_probe_technical_guard() -> None:
    module = load_robust()
    wrapped_state = state(0, 1.0, 1.0, 0)
    accepted_decision = module.decide_state_update(
        {"accept": True}, {}, wrapped_state
    )
    assert accepted_decision["incumbent_state_before"]["gradient_observation_count"] == 0
    assert accepted_decision["incumbent_state_after"]["gradient_observation_count"] == 1
    rejected_decision = module.decide_state_update(
        {"accept": False}, {}, wrapped_state
    )
    assert rejected_decision["incumbent_state_before"]["gradient_observation_count"] == 1
    assert rejected_decision["incumbent_state_after"]["gradient_observation_count"] == 1

    initial, result = transition_result()
    assert module.validate_formal_state_transitions(result, initial)["gradient_observation_count"] == 1

    bad_accepted = copy.deepcopy(result)
    bad_accepted["successful"][0]["gradient_update_applied"] = False
    expect_runtime(
        lambda: module.validate_formal_state_transitions(bad_accepted, initial),
        "accepted rank 1",
    )
    bad_rejected = copy.deepcopy(result)
    bad_rejected["successful"][1]["incumbent_state_after"]["gradient_observation_count"] = 2
    expect_runtime(
        lambda: module.validate_formal_state_transitions(bad_rejected, initial),
        "rejected/probe rank 2",
    )
    bad_probe = copy.deepcopy(result)
    bad_probe["successful"][2]["gradient_update_applied"] = True
    expect_runtime(
        lambda: module.validate_formal_state_transitions(bad_probe, initial),
        "rejected/probe rank 4",
    )
    bad_technical = copy.deepcopy(result)
    bad_technical["technical_failures"][0]["gradient_update_applied"] = True
    expect_runtime(
        lambda: module.validate_formal_state_transitions(bad_technical, initial),
        "technical rank 3",
    )


def load_formal_with_fake(fake: ModuleType) -> ModuleType:
    previous = sys.modules.get(fake.__name__)
    sys.modules[fake.__name__] = fake
    try:
        spec = importlib.util.spec_from_file_location("job_v58_formal_test", FORMAL)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(fake.__name__, None)
        else:
            sys.modules[fake.__name__] = previous
    return module


def test_formal_commit_includes_gradient_and_after_commit_verification() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        state_path = root / "state.json"
        replay = root / "replay"
        replay.mkdir()
        accepted_counter = root / "accepted.txt"
        write_json(state_path, state(0, 1.0, 1.0, 0))
        accepted_counter.write_text("0\n", encoding="utf-8")

        fake = ModuleType("job_formal_target10_batch_controller_v1")
        fake.PARAMETER_FIELDS = (
            "accepted_count",
            "incumbent_score",
            "incumbent_params",
            "range_by_parameter",
        )

        def atomic(path: Path, value: dict) -> None:
            write_json(path, dict(value))

        fake.atomic_json = atomic

        def fake_main() -> int:
            initial = json.loads(state_path.read_text(encoding="utf-8"))
            final = state(1, 2.0, 2.0, 1)
            write_json(
                replay / "summary.json",
                {
                    "complete_gradient_state_transition_guard": "passed",
                    "gradient_state_contract": "job_v58_complete_gradient_state_transition_v1",
                    "final_state": final,
                },
            )
            transaction = replay / "formal_state_commit_transaction.json"
            fake.atomic_json(
                transaction,
                {
                    "status": "prepared",
                    "accepted_before": 0,
                    "accepted_after": 1,
                    "state_sha256_before": sha(state_path),
                },
            )
            accepted_counter.write_text("1\n", encoding="utf-8")
            committed = dict(initial)
            for field in fake.PARAMETER_FIELDS:
                committed[field] = final[field]
            fake.atomic_json(state_path, committed)
            fake.atomic_json(
                transaction,
                {
                    "status": "committed",
                    "accepted_before": 0,
                    "accepted_after": 1,
                    "state_sha256_after": sha(state_path),
                },
            )
            return 0

        fake.main = fake_main
        module = load_formal_with_fake(fake)
        previous_argv = sys.argv
        sys.argv = [
            str(FORMAL),
            "--state",
            str(state_path),
            "--replay-root",
            str(replay),
            "--accepted-counter",
            str(accepted_counter),
        ]
        try:
            assert module.main() == 0
        finally:
            sys.argv = previous_argv
        committed_state = json.loads(state_path.read_text(encoding="utf-8"))
        transaction = json.loads(
            (replay / "formal_state_commit_transaction.json").read_text(encoding="utf-8")
        )
        assert committed_state["gradient_observation_count"] == 1
        assert transaction["gradient_observation_count_before"] == 0
        assert transaction["gradient_observation_count_after"] == 1
        assert transaction["gradient_observation_count_delta"] == 1
        assert transaction["gradient_state_sha256_after"] == sha(state_path)


def fixture(root: Path) -> tuple[dict, dict[str, Path]]:
    fingerprint = lambda rank: "three-level-sha256:" + f"{rank:064x}"
    params = lambda rank: {"p": float(rank)}
    state_before = {
        "accepted_count": 0,
        "incumbent_score": 1.0,
        "incumbent_params": params(1),
        "range_by_parameter": {"p": {"min": 1.0, "max": 1.0}},
    }
    state_after = {
        "accepted_count": 1,
        "incumbent_score": 2.0,
        "incumbent_params": params(2),
        "range_by_parameter": {"p": {"min": 1.0, "max": 2.0}},
    }
    successful = []
    running = copy.deepcopy(state_before)
    for rank, role in ((1, "trusted"), (2, "trusted"), (3, "trusted"), (4, "probe"), (5, "probe")):
        accepted = rank == 1
        before = copy.deepcopy(running)
        if accepted:
            running = copy.deepcopy(state_after)
        after = copy.deepcopy(running)
        successful.append(
            {
                "rank": rank,
                "selection_role": role,
                "technical_failure": False,
                "accepted": accepted,
                "gradient_update_applied": accepted,
                "incumbent_update_applied": accepted,
                "range_update_applied": accepted,
                "candidate_binding": {
                    "candidate_params": params(rank),
                    "fingerprint_id": fingerprint(rank),
                },
                "incumbent_state_before": before,
                "incumbent_state_after": after,
            }
        )
    paths = {
        "summary": root / "formal/replay/summary.json",
        "events": root / "formal/replay/events.jsonl",
        "state": root / "package/formal_state.json",
        "global_counter": root / "global.txt",
        "formal_replay_counter": root / "formal.txt",
        "accepted_counter": root / "accepted.txt",
        "transaction": root / "formal/replay/formal_state_commit_transaction.json",
        "selection": root / "formal/package/top5_promoted_formal.json",
        "gate": root / "formal/package/entry_gate.json",
        "cycle_audit": root / "cycle_001/cycle_audit.json",
        "controller_lock": root / "controller.lock",
        "formal_controller": TOOLS / "job_formal_target10_batch_controller.py",
        "robust_resource_runner": TOOLS / "run_job_round8_strict_serial_robust_v10_resource_median.py",
        "state_guard": TOOLS / "run_job_round8_strict_serial_robust_v9_state_guard.py",
        "v57_controller": TOOLS / "job_fresh10_target10_autonomous_v57.py",
    }
    paths["cycle_audit"].parent.mkdir(parents=True, exist_ok=True)
    selection = {
        "top5": [
            {
                "rank": rank,
                "selection_role": "trusted" if rank <= 3 else "probe",
                "candidate_params": params(rank),
                "three_level_fingerprint": {"id": fingerprint(rank)},
            }
            for rank in range(1, 6)
        ]
    }
    write_json(paths["selection"], selection)
    write_json(
        paths["summary"],
        {
            "diagnostic_only": False,
            "selection_sha256": sha(paths["selection"]),
            "physical_invocation_count": 5,
            "successful_real_replay_count": 5,
            "technical_failure_count": 0,
            "batch_budget_attempt_count": 5,
            "baseline_params_restored": True,
            "successful": successful,
            "technical_failures": [],
        },
    )
    paths["events"].write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in successful),
        encoding="utf-8",
    )
    formal_state = {
        **state_after,
        "gradient_observation_count": 0,
        "campaign_contract": "job_v57_fresh0_resource_median_no_retry_v1",
        "global_ordinal": 105,
        "formal_replay_count": 205,
        "last_formal_batch": {"accepted_delta": 1},
    }
    write_json(paths["state"], formal_state)
    for name, value in (("global_counter", 105), ("formal_replay_counter", 205), ("accepted_counter", 1)):
        paths[name].write_text(f"{value}\n", encoding="utf-8")
    paths["controller_lock"].write_text("", encoding="utf-8")
    write_json(
        paths["gate"],
        {
            "passed": True,
            "checks": {"all": True},
            "formal_state_sha256_before": "a" * 64,
            "novel_discovery_selection_sha256": "b" * 64,
        },
    )
    write_json(
        paths["transaction"],
        {
            "status": "committed",
            "accepted_before": 0,
            "accepted_after": 1,
            "state_sha256_before": "a" * 64,
            "state_sha256_after": sha(paths["state"]),
            "global_ordinal_after": 105,
            "formal_replay_count_after": 205,
        },
    )
    manifest = {
        "schema_version": "job_v57_to_v58_binding_manifest_v1",
        "bindings": {
            name: (
                {"path": str(path.resolve()), "must_be_absent": True}
                if name == "cycle_audit"
                else {"path": str(path.resolve()), "sha256": sha(path)}
            )
            for name, path in paths.items()
        },
    }
    return manifest, paths


def test_repair_and_every_crash_window_are_fail_closed() -> None:
    stages = (None, "prepared", "state", "cycle_audit", "transaction")
    for stage in stages:
        with tempfile.TemporaryDirectory() as directory:
            manifest, paths = fixture(Path(directory))
            observed = repair._verify_bindings(manifest, paths)
            plan = repair.build_plan(manifest, paths, observed)
            journal, audit = repair._repair_paths(paths["state"])
            counters_before = {
                name: paths[name].read_bytes()
                for name in ("global_counter", "formal_replay_counter", "accepted_counter")
            }
            if stage is None:
                result = repair.apply_plan(plan, paths, journal, audit)
                assert result["gradient_observation_count_before"] == 0
                assert result["gradient_observation_count_after"] == 1
                assert json.loads(paths["state"].read_text(encoding="utf-8"))[
                    "gradient_observation_count"
                ] == 1
                assert repair._verify_committed_repair(
                    repair.load_json(journal), paths
                )["status"] == "already_committed"
            else:
                try:
                    repair.apply_plan(
                        plan,
                        paths,
                        journal,
                        audit,
                        fail_after_stage=stage,
                    )
                except repair.RepairFailure as error:
                    assert "injected crash" in str(error)
                else:
                    raise AssertionError(f"crash stage unexpectedly completed: {stage}")
                try:
                    repair._verify_committed_repair(repair.load_json(journal), paths)
                except repair.RepairFailure as error:
                    assert "incomplete" in str(error)
                else:
                    raise AssertionError("prepared crash journal was accepted")
            for name, before in counters_before.items():
                assert paths[name].read_bytes() == before


def main() -> int:
    test_accepted_rejected_probe_technical_guard()
    test_formal_commit_includes_gradient_and_after_commit_verification()
    test_repair_and_every_crash_window_are_fail_closed()
    print("JOB v58 gradient state tests passed: 3 groups", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

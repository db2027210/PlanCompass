from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
import sys

sys.path.insert(0, str(TOOLS))
import job_fresh10_target10_autonomous_v63 as v63  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def authority() -> dict:
    return {
        "formal_state_sha256": v63.EXPECTED_FORMAL_STATE_SHA256,
        "accepted_count": 1,
        "counters": {"global": 429, "formal_replay": 211, "accepted": 1},
    }


def install_fixture(root: Path) -> tuple[Path, Path]:
    cycle = root / "cycle_002"
    recovery = cycle / "fresh_discovery_05/v61_partial_recovery_audit.json"
    discovery = cycle / "discovery_replay_05"
    discovery_selection = discovery / "top5.json"
    discovery_summary = discovery / "replay/summary.json"
    discovery_events = discovery / "replay/events.jsonl"
    discovery_audit = discovery / "diagnostic_audit.json"
    discovery_counters = discovery / "counters_before.json"
    repeat = cycle / "repeat_01"
    repeat_selection = repeat / "top5.json"

    write_json(
        recovery,
        {
            "status": "committed",
            "contract": v63.v62.v61.RECOVERY_CONTRACT,
            "new_workload_calls": 0,
            "formal_state_updates": 0,
            "counter_updates": 0,
        },
    )
    write_json(discovery_selection, {"top5": []})
    write_json(
        discovery_summary,
        {
            "physical_invocation_count": 5,
            "successful_real_replay_count": 5,
            "technical_failure_count": 0,
            "diagnostic_only": True,
            "baseline_params_restored": True,
            "initial_state": {"accepted_count": 1},
            "final_state": {"accepted_count": 1},
            "selection_sha256": sha(discovery_selection),
        },
    )
    discovery_events.write_text("", encoding="utf-8")
    write_json(
        discovery_audit,
        {
            "status": "clean5_state_neutral",
            "physical_calls": 5,
            "valid_replays": 5,
            "technical_failures": 0,
            "accepted_added": 0,
            "formal_state_sha256": v63.EXPECTED_FORMAL_STATE_SHA256,
            "summary_sha256": sha(discovery_summary),
        },
    )
    write_json(discovery_counters, {"global": 424, "formal_replay": 211, "accepted": 1})
    write_json(repeat_selection, {"top5": []})
    (repeat / "top5.second.json").write_bytes(repeat_selection.read_bytes())
    write_json(repeat / "counters_before.json", authority()["counters"])
    write_json(
        repeat / "v50_batch_sequence.json",
        {
            "schema_version": "job_v50_diagnostic_batch_sequence_v1",
            "sequence": 2,
            "phase_root": str(repeat.resolve()),
        },
    )
    return cycle, repeat


def test_exact_zero_call_boundary_is_authorized_and_controller_recovers() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cycle, repeat = install_fixture(root)
        v49 = v63.implementation.v49
        saved = (
            v49.ROOT,
            v49.validate_top5,
            v63.v62._authority_pin,
            v63.EXPECTED_REPEAT_SELECTION_SHA256,
            v63.EXPECTED_AUTHORITY_BRIDGE_SHA256,
            v63.EXPECTED_FAILED_CONTROLLER_STATE_SHA256,
        )
        try:
            v49.ROOT = root
            v49.validate_top5 = lambda *args, **kwargs: {}
            v63.v62._authority_pin = authority
            bridge = v63.v62.build_clean5_authority_bridge(cycle)
            bridge_path = cycle / "v62_v61_clean5_authority_bridge.json"
            v63.EXPECTED_REPEAT_SELECTION_SHA256 = sha(repeat / "top5.json")
            v63.EXPECTED_AUTHORITY_BRIDGE_SHA256 = sha(bridge_path)
            assert bridge["authority_before"]["counters"]["global"] == 424
            assert bridge["authority_after"]["counters"]["global"] == 429

            failure = {
                "schema_version": "job_fresh10_target10_autonomous_v62",
                "status": "fail_closed",
                "error": (
                    "partial diagnostic has no sealed summary; refusing replay: "
                    f"{repeat.resolve()}"
                ),
            }
            state_path = root / "controller_state.json"
            write_json(state_path, failure)
            v63.EXPECTED_FAILED_CONTROLLER_STATE_SHA256 = sha(state_path)

            authorization = v63.authorize_repeat_preworkload_resume_v63()
            assert authorization["physical_calls_before_resume"] == 0
            assert authorization["runner_launch_permitted_once"] is True
            assert authorization["selection_rebuilt"] is False
            recovered = v63.recover_controller_state_v63()
            assert recovered["status"] == "ready_resume"
            assert recovered["reused_repeat_selection_sha256"] == sha(
                repeat / "top5.json"
            )
            assert Path(recovered["source_fail_closed_state_backup"]).read_bytes()
            assert v63.recover_controller_state_v63() == recovered
        finally:
            (
                v49.ROOT,
                v49.validate_top5,
                v63.v62._authority_pin,
                v63.EXPECTED_REPEAT_SELECTION_SHA256,
                v63.EXPECTED_AUTHORITY_BRIDGE_SHA256,
                v63.EXPECTED_FAILED_CONTROLLER_STATE_SHA256,
            ) = saved


def test_counter_chain_counts_clean5_then_zero_call_repeat() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cycle, repeat = install_fixture(root)
        v49 = v63.implementation.v49
        saved = (
            v49.ROOT,
            v49.validate_top5,
            v49.state_pin,
            v49.counter,
            v63.v62._authority_pin,
            v63.EXPECTED_REPEAT_SELECTION_SHA256,
            v63.EXPECTED_AUTHORITY_BRIDGE_SHA256,
        )
        try:
            v49.ROOT = root
            v49.validate_top5 = lambda *args, **kwargs: {}
            v63.v62._authority_pin = authority
            v63.v62.build_clean5_authority_bridge(cycle)
            v63.EXPECTED_REPEAT_SELECTION_SHA256 = sha(repeat / "top5.json")
            v63.EXPECTED_AUTHORITY_BRIDGE_SHA256 = sha(
                cycle / "v62_v61_clean5_authority_bridge.json"
            )
            v49.state_pin = lambda: (
                v63.EXPECTED_FORMAL_STATE_SHA256,
                {"global_ordinal": 424, "formal_replay_count": 211, "accepted_count": 1},
            )
            counters = {
                v49.GLOBAL_COUNTER: 429,
                v49.FORMAL_REPLAY_COUNTER: 211,
                v49.ACCEPTED_COUNTER: 1,
            }
            v49.counter = lambda path: counters[path]
            v63.validate_pending_diagnostic_counters_v63(
                v63.implementation.HISTORICAL_INITIAL_FORMAL,
                2,
            )
            assert not (repeat / "replay").exists()
        finally:
            (
                v49.ROOT,
                v49.validate_top5,
                v49.state_pin,
                v49.counter,
                v63.v62._authority_pin,
                v63.EXPECTED_REPEAT_SELECTION_SHA256,
                v63.EXPECTED_AUTHORITY_BRIDGE_SHA256,
            ) = saved


def test_replay_directory_or_counter_drift_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cycle, repeat = install_fixture(root)
        v49 = v63.implementation.v49
        saved = (
            v49.ROOT,
            v49.validate_top5,
            v63.v62._authority_pin,
            v63.EXPECTED_REPEAT_SELECTION_SHA256,
            v63.EXPECTED_AUTHORITY_BRIDGE_SHA256,
        )
        try:
            v49.ROOT = root
            v49.validate_top5 = lambda *args, **kwargs: {}
            v63.v62._authority_pin = authority
            v63.v62.build_clean5_authority_bridge(cycle)
            v63.EXPECTED_REPEAT_SELECTION_SHA256 = sha(repeat / "top5.json")
            v63.EXPECTED_AUTHORITY_BRIDGE_SHA256 = sha(
                cycle / "v62_v61_clean5_authority_bridge.json"
            )
            (repeat / "replay").mkdir()
            try:
                v63.authorize_repeat_preworkload_resume_v63()
            except v49.FailClosed as error:
                assert "exact preworkload" in str(error)
            else:
                raise AssertionError("v63 accepted an existing replay directory")
            (repeat / "replay").rmdir()
            write_json(
                repeat / "counters_before.json",
                {"global": 428, "formal_replay": 211, "accepted": 1},
            )
            try:
                v63.authorize_repeat_preworkload_resume_v63()
            except v49.FailClosed as error:
                assert "authority binding" in str(error)
            else:
                raise AssertionError("v63 accepted counter drift")
        finally:
            (
                v49.ROOT,
                v49.validate_top5,
                v63.v62._authority_pin,
                v63.EXPECTED_REPEAT_SELECTION_SHA256,
                v63.EXPECTED_AUTHORITY_BRIDGE_SHA256,
            ) = saved


def test_source_and_launcher_pin_exact_remote_authority() -> None:
    controller = TOOLS / "job_fresh10_target10_autonomous_v63.py"
    launcher = TOOLS / "launch_job_target10_autonomous_v63.sh"
    source = controller.read_text(encoding="utf-8")
    launch = launcher.read_text(encoding="utf-8")
    assert "validate_pending_diagnostic_counters_v63" in source
    assert "return _RESUME_V60()" in source
    assert v63.EXPECTED_FAILED_CONTROLLER_STATE_SHA256 in launch
    assert v63.EXPECTED_REPEAT_SELECTION_SHA256 in launch
    assert v63.EXPECTED_AUTHORITY_BRIDGE_SHA256 in launch
    assert hashlib.sha256(controller.read_bytes()).hexdigest() in launch
    assert 'test ! -e "${REPEAT}/replay"' in launch
    assert "V63_PREFLIGHT_ONLY" in launch


if __name__ == "__main__":
    test_exact_zero_call_boundary_is_authorized_and_controller_recovers()
    test_counter_chain_counts_clean5_then_zero_call_repeat()
    test_replay_directory_or_counter_drift_fails_closed()
    test_source_and_launcher_pin_exact_remote_authority()
    print("JOB v63 preworkload resume tests passed: 4", flush=True)

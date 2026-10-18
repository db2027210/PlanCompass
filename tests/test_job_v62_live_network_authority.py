from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
import sys

sys.path.insert(0, str(TOOLS))
import job_fresh10_target10_autonomous_v62 as v62  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def network(containers: dict | None = None) -> list[dict]:
    return [
        {
            "Name": v62.NETWORK_NAME,
            "Driver": "bridge",
            "Scope": "local",
            "Internal": False,
            "Containers": containers or {},
            "Options": {
                "com.docker.network.bridge.name": v62.BRIDGE_INTERFACE,
            },
        }
    ]


def authority(global_counter: int = 429) -> dict:
    return {
        "formal_state_sha256": "8" * 64,
        "accepted_count": 1,
        "counters": {
            "global": global_counter,
            "formal_replay": 211,
            "accepted": 1,
        },
    }


def test_each_live_preflight_binds_current_counter_and_exact_empty_bridge() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        original_authority = v62._authority_pin
        original_inspect = v62._inspect_network
        original_environment = dict(os.environ)
        current = {"value": authority(424)}
        try:
            v62._authority_pin = lambda: current["value"]
            v62._inspect_network = lambda: network()
            first = v62.install_live_network_preflight(root / "first")
            assert first["global_counter"] == 424
            validated = v62.validate_live_network_preflight()
            assert validated["global_counter"] == 424

            current["value"] = authority(429)
            try:
                v62.validate_live_network_preflight()
            except v62.implementation.v49.FailClosed as error:
                assert "current authority" in str(error)
            else:
                raise AssertionError("v62 accepted stale global=424 preflight at global=429")
            second = v62.install_live_network_preflight(root / "second")
            assert second["global_counter"] == 429
            assert second["audit_path"] != first["audit_path"]
            assert v62.validate_live_network_preflight()["global_counter"] == 429

            v62._inspect_network = lambda: network({"container": {}})
            try:
                v62.seal_live_network_preflight(root / "nonempty")
            except v62.implementation.v49.FailClosed as error:
                assert "empty-container" in str(error)
            else:
                raise AssertionError("v62 accepted a nonempty dedicated bridge")
        finally:
            v62._authority_pin = original_authority
            v62._inspect_network = original_inspect
            os.environ.clear()
            os.environ.update(original_environment)


def install_clean5_evidence(root: Path) -> Path:
    cycle = root / "cycle_002"
    recovery = cycle / "fresh_discovery_05/v61_partial_recovery_audit.json"
    phase = cycle / "discovery_replay_05"
    selection = phase / "top5.json"
    summary = phase / "replay/summary.json"
    events = phase / "replay/events.jsonl"
    diagnostic = phase / "diagnostic_audit.json"
    counters = phase / "counters_before.json"
    repeat = cycle / "repeat_01/top5.json"
    write_json(
        recovery,
        {
            "status": "committed",
            "contract": v62.v61.RECOVERY_CONTRACT,
            "new_workload_calls": 0,
            "formal_state_updates": 0,
            "counter_updates": 0,
        },
    )
    write_json(selection, {"top5": []})
    write_json(
        summary,
        {
            "physical_invocation_count": 5,
            "successful_real_replay_count": 5,
            "technical_failure_count": 0,
            "diagnostic_only": True,
            "baseline_params_restored": True,
            "initial_state": {"accepted_count": 1},
            "final_state": {"accepted_count": 1},
            "selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
        },
    )
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text("", encoding="utf-8")
    write_json(
        diagnostic,
        {
            "status": "clean5_state_neutral",
            "physical_calls": 5,
            "valid_replays": 5,
            "technical_failures": 0,
            "accepted_added": 0,
            "formal_state_sha256": "8" * 64,
            "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
        },
    )
    write_json(counters, {"global": 424, "formal_replay": 211, "accepted": 1})
    write_json(repeat, {"top5": []})
    return cycle


def test_clean5_bridge_and_fail_closed_controller_recovery_are_zero_call() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cycle = install_clean5_evidence(root)
        v49 = v62.implementation.v49
        old_root = v49.ROOT
        old_validate = v49.validate_top5
        old_authority = v62._authority_pin
        try:
            v49.ROOT = root
            v49.validate_top5 = lambda *args, **kwargs: {}
            v62._authority_pin = lambda: authority(429)
            write_json(
                root / "controller_state.json",
                {
                    "schema_version": "job_fresh10_target10_autonomous_v61",
                    "status": "fail_closed",
                    "error": "v59 dedicated network preflight contract failed",
                },
            )
            bridge = v62.build_clean5_authority_bridge(cycle)
            assert bridge["authority_before"]["counters"]["global"] == 424
            assert bridge["authority_after"]["counters"]["global"] == 429
            assert bridge["physical_transition"] == 5
            assert bridge["reuse_repeat_01"] is True
            recovered = v62.recover_controller_state_v62(cycle)
            assert recovered["status"] == "ready_resume"
            assert recovered["phase"] == "repeat_gate"
            assert recovered["new_workload_calls"] == 0
            assert recovered["counter_updates"] == 0
            assert Path(recovered["source_fail_closed_state_backup"]).is_file()
            assert v62.recover_controller_state_v62(cycle) == recovered
        finally:
            v49.ROOT = old_root
            v49.validate_top5 = old_validate
            v62._authority_pin = old_authority


def test_runner_boundary_refreshes_preflight_after_counter_advance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        original_authority = v62._authority_pin
        original_inspect = v62._inspect_network
        original_boundary = v62.v61.v60._is_runner_boundary
        original_run = v62.v61.v60.run_logged_with_network_environment
        original_environment = dict(os.environ)
        current = {"global": 424}
        observed: list[tuple[str, int]] = []
        try:
            v62._authority_pin = lambda: authority(current["global"])
            v62._inspect_network = lambda: network()
            v62.v61.v60._is_runner_boundary = lambda command: True

            def fake_run(command, log_path, **kwargs):
                audit_path = Path(os.environ["RA_JOB_NETWORK_PREFLIGHT_JSON"])
                value = json.loads(audit_path.read_text(encoding="utf-8"))
                observed.append((str(audit_path), int(value["global_counter"])))
                return 0

            v62.v61.v60.run_logged_with_network_environment = fake_run
            v62.run_logged_v62(["python", "runner.py"], root / "one/runner.log", timeout_seconds=1)
            current["global"] = 429
            v62.run_logged_v62(["python", "runner.py"], root / "two/runner.log", timeout_seconds=1)
            assert [value for _path, value in observed] == [424, 429]
            assert observed[0][0] != observed[1][0]
        finally:
            v62._authority_pin = original_authority
            v62._inspect_network = original_inspect
            v62.v61.v60._is_runner_boundary = original_boundary
            v62.v61.v60.run_logged_with_network_environment = original_run
            os.environ.clear()
            os.environ.update(original_environment)


def test_source_uses_v60_resume_without_revalidating_stale_v61_authority() -> None:
    source = (TOOLS / "job_fresh10_target10_autonomous_v62.py").read_text(
        encoding="utf-8"
    )
    assert "return _RESUME_V60()" in source
    assert "seal_live_network_preflight" in source
    assert "v62_live_runner_preflights" in source
    assert "len(containers) != 0" in source
    assert 'network.get("Driver") != "bridge"' in source
    assert "same-rank" not in source


def test_launcher_uses_current_429_and_no_static_network_audit() -> None:
    controller = TOOLS / "job_fresh10_target10_autonomous_v62.py"
    launcher = TOOLS / "launch_job_target10_autonomous_v62.sh"
    source = launcher.read_text(encoding="utf-8")
    controller_sha = hashlib.sha256(controller.read_bytes()).hexdigest()
    assert controller_sha in source
    assert 'test "${GLOBAL_BEFORE}" = 429' in source
    assert "v62_v61_clean5_authority_bridge.json" in source
    assert "cycle_002/repeat_01/top5.json" not in source
    assert '${CYCLE}/repeat_01/top5.json' in source
    assert "NETWORK_AUDIT" not in source
    assert "docker network inspect" not in source
    assert 'V62_PREFLIGHT_ONLY:-0' in source


def test_deployment_manifest_sha_pins_every_local_artifact() -> None:
    manifest = json.loads(
        (TOOLS / "job_v62_deployment_manifest.json").read_text(encoding="utf-8")
    )
    entries = [
        manifest["controller"],
        manifest["launcher"],
        manifest["tests"],
        *manifest["sealed_dependencies"],
    ]
    for entry in entries:
        path = ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], path
    assert manifest["status"] == "sealed_offline_not_launched"
    assert manifest["network_preflight_contract"]["checks_relaxed"] is False
    assert manifest["safety"]["workload_calls"] == 0
    assert manifest["safety"]["mysql_touched"] is False


if __name__ == "__main__":
    test_each_live_preflight_binds_current_counter_and_exact_empty_bridge()
    test_clean5_bridge_and_fail_closed_controller_recovery_are_zero_call()
    test_runner_boundary_refreshes_preflight_after_counter_advance()
    test_source_uses_v60_resume_without_revalidating_stale_v61_authority()
    test_launcher_uses_current_429_and_no_static_network_audit()
    test_deployment_manifest_sha_pins_every_local_artifact()
    print("JOB v62 live network authority tests passed: 6", flush=True)

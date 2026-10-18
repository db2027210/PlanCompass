from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
V58 = TOOLS / "job_fresh10_target10_autonomous_v58.py"
V59 = TOOLS / "job_fresh10_target10_autonomous_v59.py"
LAUNCHER = TOOLS / "launch_job_target10_autonomous_v59.sh"


class FailClosed(RuntimeError):
    pass


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(root: Path) -> ModuleType:
    state_path = root / "package/formal_state.json"
    global_counter = root / "global.txt"
    formal_counter = root / "formal.txt"
    accepted_counter = root / "accepted.txt"
    write_json(state_path, {"accepted_count": 1})
    global_counter.write_text("424\n", encoding="utf-8")
    formal_counter.write_text("211\n", encoding="utf-8")
    accepted_counter.write_text("1\n", encoding="utf-8")

    def read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def atomic_json(path: Path, value: dict) -> None:
        write_json(path, dict(value))

    def counter(path: Path) -> int:
        return int(path.read_text(encoding="utf-8").strip())

    def fingerprint_set(path: Path) -> set[str]:
        value = read_json(path)
        return {
            str(item["three_level_fingerprint"]["id"])
            for item in value["top5"]
        }

    v49 = SimpleNamespace(
        FailClosed=FailClosed,
        ROOT=root,
        STATE=state_path,
        GLOBAL_COUNTER=global_counter,
        FORMAL_REPLAY_COUNTER=formal_counter,
        ACCEPTED_COUNTER=accepted_counter,
        sha256_file=sha,
        read_json=read_json,
        atomic_json=atomic_json,
        counter=counter,
        state_pin=lambda: (sha(state_path), read_json(state_path)),
        fingerprint_set=fingerprint_set,
        validate_top5=lambda *args, **kwargs: None,
    )
    implementation = SimpleNamespace(
        v49=v49,
        CONTROLLER_VERSION="v58",
        resume_position_v50=lambda: (root / "cycle_001/formal", 2),
        generate_fresh_discovery=lambda *args, **kwargs: args[0]
        / f"fresh_discovery_{args[1]:02d}/top5.json",
        run_diagnostic_v50=lambda *args, **kwargs: (False, {}),
        require_v50_hashes=lambda: None,
    )
    fake = ModuleType("job_fresh10_target10_autonomous_v58")
    fake.__file__ = str(V58)
    fake.implementation = implementation
    previous = sys.modules.get(fake.__name__)
    sys.modules[fake.__name__] = fake
    try:
        spec = importlib.util.spec_from_file_location("job_v59_network_test", V59)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(fake.__name__, None)
        else:
            sys.modules[fake.__name__] = previous
    return module


def selection(attempt: int) -> dict:
    return {
        "top5": [
            {
                "three_level_fingerprint": {
                    "id": "three-level-sha256:" + f"{attempt * 10 + rank:064x}"
                }
            }
            for rank in range(1, 6)
        ]
    }


def network_summary() -> dict:
    return {
        "diagnostic_only": True,
        "physical_invocation_count": 0,
        "successful_real_replay_count": 0,
        "technical_failure_count": 1,
        "pre_invocation_technical_failure_count": 1,
        "baseline_params_restored": True,
        "initial_state": {"accepted_count": 1},
        "final_state": {"accepted_count": 1},
        "technical_failures": [
            {
                "accepted": False,
                "gradient_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
                "error": "invalid or prohibited RA_JOB_DOCKER_NETWORK",
            }
        ],
    }


def build_historical_failures(module: ModuleType, root: Path) -> None:
    state_sha = sha(root / "package/formal_state.json")
    for attempt in (1, 2, 3):
        replay_root = root / f"cycle_002/discovery_replay_{attempt:02d}"
        fresh_root = root / f"cycle_002/fresh_discovery_{attempt:02d}"
        write_json(replay_root / "top5.json", selection(attempt))
        write_json(replay_root / "replay/summary.json", network_summary())
        (replay_root / "replay/events.jsonl").write_text("\n", encoding="utf-8")
        (replay_root / "runner.log").write_text(
            "invalid or prohibited RA_JOB_DOCKER_NETWORK\n",
            encoding="utf-8",
        )
        write_json(
            replay_root / "diagnostic_audit.json",
            {
                "status": "technical_state_neutral",
                "physical_calls": 0,
                "accepted_added": 0,
                "formal_state_sha256": state_sha,
            },
        )
        write_json(
            replay_root / "counters_before.json",
            {"global": 424, "formal_replay": 211, "accepted": 1},
        )
        write_json(fresh_root / "top5.json", selection(attempt))
        write_json(fresh_root / "top5.second.json", selection(attempt))
        write_json(
            fresh_root / "v55_successor_budget.json",
            {
                "status": "replay_handoff_started",
                "selection_path": str((fresh_root / "top5.json").resolve()),
                "selection_sha256": sha(fresh_root / "top5.json"),
                "deadline_unix_seconds": 1,
                "invocations": [
                    {"slot": "historical_training_a.json", "status": "complete"},
                    {"slot": "historical_training_b.json", "status": "complete"},
                ],
            },
        )


def test_network_preflight_and_quarantine() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        module = load_module(root)
        build_historical_failures(module, root)
        audit = root / "network.json"
        inspect = root / "network-inspect.json"
        write_json(
            inspect,
            [
                {
                    "Name": "ra_job_v53_net",
                    "Driver": "bridge",
                    "Scope": "local",
                    "Internal": False,
                    "Containers": {},
                    "Options": {
                        "com.docker.network.bridge.name": "ra-job-v53",
                    },
                }
            ],
        )
        write_json(
            audit,
            {
                "contract": "job_v59_dedicated_bridge_preflight_v1",
                "network_name": "ra_job_v53_net",
                "driver": "bridge",
                "scope": "local",
                "internal": False,
                "bridge_interface": "ra-job-v53",
                "attached_container_count": 0,
                "network_inspect_path": str(inspect.resolve()),
                "network_inspect_sha256": sha(inspect),
                "formal_state_sha256": sha(root / "package/formal_state.json"),
                "accepted_count": 1,
                "global_counter": 424,
                "formal_replay_counter": 211,
                "accepted_counter": 1,
            },
        )
        old_environment = dict(os.environ)
        os.environ["RA_JOB_DOCKER_NETWORK"] = "ra_job_v53_net"
        os.environ["RA_JOB_NETWORK_PREFLIGHT_JSON"] = str(audit)
        os.environ["RA_JOB_NETWORK_PREFLIGHT_SHA256"] = sha(audit)
        try:
            assert module._network_preflight()["attached_container_count"] == 0
            quarantine = module.ensure_network_quarantine(root / "cycle_002")
        finally:
            os.environ.clear()
            os.environ.update(old_environment)
        assert quarantine["repeated_signature_count"] == 3
        assert quarantine["physical_workload_calls"] == 0
        assert quarantine["ordinal_claims"] == 0
        assert quarantine["accepted_added"] == 0
        assert len(quarantine["quarantined_fingerprints"]) == 15


def test_expired_sealed_successors_are_reused_without_generation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        module = load_module(root)
        build_historical_failures(module, root)
        module._GENERATE_V58 = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sealed successor was regenerated")
        )
        for attempt in (1, 2, 3):
            result = module.generate_fresh_discovery_v59(root / "cycle_002", attempt)
            assert result == root / f"cycle_002/fresh_discovery_{attempt:02d}/top5.json"


def test_fourth_same_signature_fails_closed_without_attempt_five() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        module = load_module(root)
        build_historical_failures(module, root)
        cycle = root / "cycle_002"
        quarantine = module.ensure_network_quarantine(cycle)
        assert quarantine["repeated_signature_count"] == 3
        phase = cycle / "discovery_replay_04"
        write_json(phase / "replay/summary.json", network_summary())
        (phase / "runner.log").write_text(
            "invalid or prohibited RA_JOB_DOCKER_NETWORK\n",
            encoding="utf-8",
        )
        module._RUN_DIAGNOSTIC_V58 = lambda *args, **kwargs: (
            False,
            network_summary(),
        )
        try:
            module.run_diagnostic_v59(phase / "top5.json", phase / "replay", phase)
        except FailClosed as error:
            assert "threshold reached" in str(error)
        else:
            raise AssertionError("fourth repeated network signature did not fail closed")
        assert not (cycle / "fresh_discovery_05").exists()


def test_launcher_restores_dedicated_network_contract() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert sha(V59) in source
    for required in (
        "JOB_NETWORK=ra_job_v53_net",
        "JOB_BRIDGE_INTERFACE=ra-job-v53",
        'docker network inspect "${JOB_NETWORK}"',
        'export RA_JOB_DOCKER_NETWORK="${JOB_NETWORK}"',
        "RA_JOB_NETWORK_PREFLIGHT_JSON",
        "RA_JOB_NETWORK_PREFLIGHT_SHA256",
        "network_inspect_sha256",
        "(.[0].Containers | length) == 0",
        'V59_PREFLIGHT_ONLY:-0',
        'sha256sum "${STATE}"',
        'formal_search_replay_counter.txt',
    ):
        assert required in source, required


def main() -> int:
    test_network_preflight_and_quarantine()
    test_expired_sealed_successors_are_reused_without_generation()
    test_fourth_same_signature_fails_closed_without_attempt_five()
    test_launcher_restores_dedicated_network_contract()
    print("JOB v59 network restart tests passed: 4", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

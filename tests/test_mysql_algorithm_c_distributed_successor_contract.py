from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = (
    ROOT / "shell/verify_mysql_algorithm_c_distributed_successor_contract.py"
)
CONTRACT_PATH = (
    ROOT
    / "preflight/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
)
SPEC = importlib.util.spec_from_file_location("distributed_successor_verifier", VERIFIER_PATH)
assert SPEC and SPEC.loader
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def _write_contract(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_canonical_contract_passes() -> None:
    result = VERIFIER.verify_contract(CONTRACT_PATH)
    assert result["ok"], result["errors"]
    assert result["scenario_count"] == 4
    assert result["remote_actions"] == 0
    assert result["formal_launch_authorized"] is False


def test_cpu_overlap_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    payload["scenarios"]["xp"]["resource_envelope"]["cpuset_cpus"] = "20-33,56-69"
    result = VERIFIER.verify_contract(_write_contract(tmp_path, payload))
    assert not result["ok"]
    assert any("placement mismatch" in error for error in result["errors"])
    assert any("logical CPU sets overlap" in error for error in result["errors"])


def test_device_overlap_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    payload["scenarios"]["tpcc"]["storage"]["device"] = "/dev/nvme0n1"
    result = VERIFIER.verify_contract(_write_contract(tmp_path, payload))
    assert not result["ok"]
    assert any("devices overlap" in error for error in result["errors"])


def test_cross_engine_comparison_cannot_be_enabled(tmp_path: Path) -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    payload["cross_engine_comparison_prohibited"] = False
    result = VERIFIER.verify_contract(_write_contract(tmp_path, payload))
    assert not result["ok"]
    assert any("cross-engine comparison" in error for error in result["errors"])


def test_dynamic_gate_cannot_be_shortened_or_reused(tmp_path: Path) -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    dynamic = payload["same_host_parallel_policy"]["dynamic_bidirectional_gate"]
    dynamic["duration_seconds"] = 60
    dynamic["authorization_reuse"] = "ALLOWED"
    result = VERIFIER.verify_contract(_write_contract(tmp_path, payload))
    assert not result["ok"]
    assert any("180 seconds" in error for error in result["errors"])
    assert any("must not be reused" in error for error in result["errors"])


def test_offline_contract_cannot_authorize_launch(tmp_path: Path) -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    payload["formal_launch_authorized"] = True
    result = VERIFIER.verify_contract(_write_contract(tmp_path, payload))
    assert not result["ok"]
    assert any("must not authorize formal launch" in error for error in result["errors"])

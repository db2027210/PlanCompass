import hashlib
import json
from pathlib import Path

from scripts import reset_mysql_xp_data_planes_v6 as reset
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v10 as runtime


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = runtime.OPTIMIZATION_CONTRACT


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v51_contract_binds_reset_successor_and_preserves_algorithm_rules():
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    bindings = value["bindings"]
    unchanged = value["unchanged_contracts"]
    optimized = value["optimized_reset"]
    assert value["schema_version"] == (
        "mysql_algorithm_c_outer_runtime_optimization_v51"
    )
    assert value["status"] == "FROZEN_RUNTIME_VALIDATED"
    assert bindings["reset_source_path"] == (
        "scripts/reset_mysql_xp_data_planes_v6.py"
    )
    assert sha(ROOT / bindings["reset_source_path"]) == (
        bindings["reset_source_sha256"]
    )
    assert sha(ROOT / bindings["stopped_discard_test_path"]) == (
        bindings["stopped_discard_test_sha256"]
    )
    assert optimized["bound_host_pid_kill_exit_wait_seconds"] == 180
    assert optimized["stopped_discard_predecessor_requires_zero_session_proof"]
    assert unchanged["fixed_top_k"] == 5
    assert unchanged["maximum_interval_ratio"] == 1.01
    assert unchanged["required_consecutive_stable_accepted_rounds"] == 2
    assert unchanged["hard_gates_and_coverage_unchanged"] is True


def test_runtime_activates_exact_reset_successor():
    source = Path(reset.__file__).resolve()
    assert source == ROOT / "scripts/reset_mysql_xp_data_planes_v6.py"
    assert reset.HOST_PID_KILL_EXIT_WAIT_SECONDS == 180

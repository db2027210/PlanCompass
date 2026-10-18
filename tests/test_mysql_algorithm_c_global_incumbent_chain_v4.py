from pathlib import Path

from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v4 as audit


HISTORY = Path("history/mysql_four_scene_20260809")
RUNS = (
    HISTORY / "v1255_to_c_full_docker_path_resilience_v111_20260906_job",
    HISTORY / "v1255_to_c_full_docker_path_resilience_v111_20260906_tpch",
    HISTORY / "v1267_to_c_nested_measurement_scope_v115_20260907_xp_cpu",
    HISTORY / "v1271_to_c_xp_io_checkpoint_runtime_v117_20260907_xp_io",
)


def test_all_current_scene_lineages_pass() -> None:
    for run in RUNS:
        result = audit.audit_run(run)
        assert result["status"] == "PASS", (run, result["failures"])
        assert result["failures"] == []
        assert result["sealed_outer1_lineage_anchor_supported"] is True


import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "config/formal/frozen_mysql_three_workloads_20260809_v2"
PREDECESSOR = ROOT / "config/formal/frozen_mysql_three_workloads_20260806_v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_successor_is_content_addressed_and_does_not_grant_launch():
    manifest = json.loads((FREEZE / "manifest.json").read_text())
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["formal_launch_authorized"] is False
    assert manifest["launch_performed"] is False
    assert set(manifest["accepted_rounds"]) == {"job", "tpch", "hybench_xp"}
    assert set(manifest["accepted_rounds"].values()) == {0}
    assert manifest["predecessor"]["sha256"] == sha(PREDECESSOR / "manifest.json")
    for name, expected in manifest["inherited_immutable_artifacts"].items():
        assert sha(PREDECESSOR / name) == expected
    assert (
        sha(FREEZE / "disk_concurrency_policy.json")
        == manifest["successor_artifacts"]["disk_concurrency_policy.json"]
    )
    assert sha(ROOT / "same_ssd_concurrency.py") == manifest["successor_artifacts"]["same_ssd_concurrency.py"]


def test_current_workers_remain_serial_inside_each_scene():
    policy = json.loads((FREEZE / "disk_concurrency_policy.json").read_text())
    assert policy["inter_scene"]["maximum_parallel_scenes"] == 3
    assert policy["same_physical_ssd_replay"]["tiers"] == [1, 4, 8, 12]
    assert policy["same_physical_ssd_replay"]["automatic_16_prohibited"] is True
    assert policy["same_physical_ssd_replay"]["effective_cap_by_fixed_top5"] == 5
    assert policy["same_physical_ssd_replay"]["same_mysql_instance_parallel_candidate_replay_prohibited"] is True
    for scene in ("job", "tpch", "hybench_xp"):
        row = policy["current_readback"][scene]
        assert row["independent_replay_workers"] == 1
        assert row["effective_replay_parallelism"] == 1
        assert row["adaptive_same_ssd_enabled"] is False


def test_workload_level_concurrency_and_native_xp_mix_are_unchanged():
    policy = json.loads((FREEZE / "disk_concurrency_policy.json").read_text())
    frozen = policy["workload_threads_unchanged"]
    assert frozen["job_workers"] == 16
    assert frozen["job_maximum_concurrent_long_queries"] == 4
    assert frozen["tpch_lanes"] == 5
    assert frozen["hybench_xp_native_iq1_iq7_uniform_random"] is True
    assert frozen["hybench_xp_at_mix_percent"] == "35/25/15/15/7/3"
    assert frozen["hybench_xp_xap_xtp_ratio_frozen"] is True

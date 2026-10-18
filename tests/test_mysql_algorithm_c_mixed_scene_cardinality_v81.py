from pathlib import Path

from scripts import run_mysql_algorithm_c_async_scene_v81 as controller
from scripts import run_mysql_four_scene_r0_cpu_a_slots_mixed_parallel_v1 as r0


ROOT = Path(__file__).resolve().parents[1]


def test_mixed_r0_source_has_formal_scene_cardinalities() -> None:
    assert '(4 if AUTHORITY_SCENE_SCOPE in {"hybench_xp_cpu", "hybench_xp_io"} else 2)' in r0._source


def test_v81_scene_worker_map_and_contract() -> None:
    assert controller.SCENE_WORKERS == {
        "job_memory": 2,
        "tpch_sf4_memory": 2,
        "hybench_xp_cpu": 4,
        "hybench_xp_io": 4,
    }
    document = controller.validate_contract()
    assert document["successor"]["release_version"] == "v81"


def test_mixed_adapter_exports_runtime_authority_api() -> None:
    assert callable(r0.configure_runtime_authority)
    assert callable(r0._validate_authority)

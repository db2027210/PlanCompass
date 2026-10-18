from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/preflight_mysql_four_scene_v3.py"


def _load():
    spec = importlib.util.spec_from_file_location("preflight_mysql_four_scene_v3", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cpuset_parser_and_compressor_round_trip() -> None:
    module = _load()
    cpus = module._cpus("1-3,7,9-10")
    assert cpus == {1, 2, 3, 7, 9, 10}
    assert module._compress(cpus) == "1-3,7,9-10"


def test_process_patterns_cover_active_database_benchmark_controllers() -> None:
    module = _load()
    commands = [
        "python binary_plan_resource_tuner.py --config x",
        "python formal_mysql_sgd_campaign.py --config x",
        "python generic_workload_shim.py -t runxp",
        "java com.hybench.HyBench -t runxp",
        "java -jar benchbase.jar -b tpch",
    ]
    for command in commands:
        assert any(pattern.search(command) for pattern in module.RELEVANT_PROCESS_PATTERNS)

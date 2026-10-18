from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/adopt_mysql_four_scene_v5.py"


def _load():
    spec = importlib.util.spec_from_file_location("adopt_mysql_four_scene_v5", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v5_adopter_is_fail_closed_before_workload() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '"workload_started": False' in source
    assert '"fresh_r0_complete": False' in source
    assert '"formal_outer_started": False' in source
    assert '"--cpuset-cpus", str(worker["database_cpuset"])' in source
    assert '"--cpuset-mems", str(worker["database_cpuset_mems"])' in source
    assert "_kernel_resource_readback(inspect, worker)" in source
    assert "_enforce_blkio(inspect, freeze, worker)" in source


def test_v5_adopter_defaults_exist() -> None:
    module = _load()
    assert module.DEFAULT_FREEZE.name == "four_scene_effective_resource_successor.json"
    assert module.DEFAULT_PREFLIGHT.name == "runtime_preflight_v5.json"

from __future__ import annotations

import json
from pathlib import Path

from mysql_algorithm_b_production_services import build


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config/formal/mysql_sgd_production/hybench_xp_io.algorithm_c.optimizer.json"


def test_xp_algorithm_c_factory_build_is_wired_without_starting_pressure() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    bundle = build(config)
    profile = json.loads((ROOT / config["profile"]).read_text(encoding="utf-8"))
    assert config["execution_authorized"] is False
    assert bundle["registration"]["id"].startswith("hybench_xp_io.")
    assert bundle["registration"]["evidence"]["campaign_method_benchmark_scenario"]["method"] == "experience_table_sgd"
    assert callable(bundle["r0_measurement"])
    assert callable(bundle["campaign_cleanup"])
    assert bundle["dependencies"].session_factories.keys() == {"hybench_xp"}
    assert bundle["service_owner"].active is None
    assert bundle["service_owner"].planner is None
    owner = bundle["service_owner"]
    for name in ("cpuset", "mems", "cpu_cores", "memory_bytes", "pids", "read_bps", "write_bps", "buffer_pool_bytes"):
        assert getattr(owner.lane, name) == profile[name]
    assert owner.output == ROOT / "history/mysql_sgd_algorithm_c/hybench_xp_io"

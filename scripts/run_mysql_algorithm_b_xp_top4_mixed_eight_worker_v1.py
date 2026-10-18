#!/usr/bin/env python3
"""Eight-worker Top4 Algorithm-B runtime for the two XP scenes."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v1.py"
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "Run one four-scene Top4-B scene on mixed-SSD w1-w4 lanes.":
        "Run one Top4-B XP scene on eight mixed-SSD lanes.",
    "mysql_algorithm_b_fresh_parallel_4x_scheduler_v26 as scheduler_4x":
        "mysql_algorithm_b_fresh_parallel_8x_scheduler_v27 as scheduler_8x",
    "reset_mysql_xp_data_planes_mixed_four_worker_v1 as reset_mixed":
        "reset_mysql_xp_data_planes_mixed_eight_worker_v1 as reset_mixed",
    "run_mysql_four_scene_mixed_four_worker_authority_v1 as authority":
        "run_mysql_four_scene_mixed_eight_worker_authority_v1 as authority",
    "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3\"\n    / \"four_scene_effective_resource_successor.json":
        "frozen_mysql_xp_eight_worker_20260827_v1\"\n    / \"xp_effective_resource_successor.json",
    'FREEZE_SCHEMA = "mysql_four_scene_mixed_disk_four_worker_successor_v3"':
        'FREEZE_SCHEMA = "mysql_xp_eight_worker_resource_successor_v1"',
    "frozen_mysql_four_scene_top4_algorithm_b_mixed_four_worker_20260827_v14":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v1",
    "if len(requested) > 4 or len(scenes) != 1:":
        "if len(requested) > 8 or len(scenes) != 1:",
    '!= "mysql_algorithm_b_four_scene_top4_mixed_four_worker_contract_v1"':
        '!= "mysql_algorithm_b_xp_top4_mixed_eight_worker_contract_v1"',
    'or contract.get("candidate_batches") != [[1, 2, 3, 4], [5]]':
        'or contract.get("candidate_batches") != [[1, 2, 3, 4, 5]]',
    'or contract.get("physical_replay_parallelism") != 4':
        'or contract.get("physical_replay_parallelism") != 8',
    '"formal_candidate_batches": [[1, 2, 3, 4], [5]],':
        '"formal_candidate_batches": [[1, 2, 3, 4, 5]],',
    '"candidate_parallelism": 4, "physical_replay_parallelism": 4,':
        '"candidate_parallelism": 5, "physical_replay_parallelism": 8,',
    '!= "mysql_algorithm_b_top4_mixed_four_worker_no_lease_coordination_v1"':
        '!= "mysql_algorithm_b_top4_mixed_eight_worker_no_lease_coordination_v1"',
    'or contract.get("maximum_workers_per_scene") != 4':
        'or contract.get("maximum_workers_per_scene") != 8',
    'for prefix in ("job", "tpch", "xp_cpu", "xp_io")\n            for index in range(1, 5)':
        'for prefix in ("xp_cpu", "xp_io")\n            for index in range(1, 9)',
    'for prefix in ("xp_cpu", "xp_io")\n            for index in range(1, 5)':
        'for prefix in ("xp_cpu", "xp_io")\n            for index in range(1, 9)',
    "alg.XP_RESET_MAX_WORKERS = 4": "alg.XP_RESET_MAX_WORKERS = 8",
    'impl["PHYSICAL_REPLAY_PARALLELISM"] = 4':
        'impl["PHYSICAL_REPLAY_PARALLELISM"] = 8',
    'impl["scheduler"] = scheduler_4x': 'impl["scheduler"] = scheduler_8x',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"eight-worker B predecessor differs: {old!r} count={text.count(old)}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_mixed_eight_worker_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value

_predecessor_install_runtime = implementation["install_runtime"]


def install_runtime() -> Any:
    core = _predecessor_install_runtime()
    impl = core._impl
    original_configure = impl["configure_runtime"]

    def configure_runtime():
        freeze = impl["alg"].load(impl["FREEZE"])
        workers = {
            str(row["worker_id"]): dict(row)
            for row in freeze.get("workers") or []
        }
        implementation["reset_mixed"].install_for_workers(workers)
        return original_configure()

    impl["configure_runtime"] = configure_runtime
    return core


implementation["install_runtime"] = install_runtime
globals()["install_runtime"] = install_runtime


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

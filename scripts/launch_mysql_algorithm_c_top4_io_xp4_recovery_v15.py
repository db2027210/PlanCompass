#!/usr/bin/env python3
"""Launch v21 IO C on the unified v3 combined freeze."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "scripts/launch_mysql_algorithm_c_top4_io_xp4_recovery_v1.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_algorithm_c_xp4_handoff_20260826_v2":
        "frozen_mysql_xp_cpu_disjoint_slots_20260826_v3",
    "xp_four_worker_resource_successor.json": "resource_successor.json",
    'HISTORY / "v164_to_xp4_live_authority_20260826" / SCENE':
        'HISTORY / "v217_to_xp_io_combined_live_authority_20260826" / SCENE',
    'HISTORY / "v172_to_c_top4_io_xp4_recovery_20260826"':
        'HISTORY / "v219_to_c_top4_io_xp4_recovery_20260826"',
    '"v172_to_c_top4_io_xp4_20260826_attempt006_xp_io"':
        '"v219_to_c_top4_io_xp4_20260826_attempt019_xp_io"',
    'ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v1.py"':
        'ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v14.py"',
    '"mysql-algc-top4-io-xp4-v172.service"':
        '"mysql-algc-top4-io-xp4-v219.service"',
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"IO C v21 launcher predecessor fragment differs: {old}")
    _source = _source.replace(old, new)
fragment = (
    '"/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,\n'
    '        str(PYTHON), str(CONTROLLER), "--run-id", RUN_ID,'
)
successor = (
    '"/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,\n'
    '        "/usr/bin/env", f"MYSQL_C_VERIFIED_BASELINE={VERIFIED_BASELINE}",\n'
    '        str(PYTHON), str(CONTROLLER), "--run-id", RUN_ID,'
)
if _source.count(fragment) != 1:
    raise RuntimeError("IO C v21 verified baseline fragment differs")
_source = _source.replace(fragment, successor)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_algorithm_c_top4_io_xp4_recovery_v15_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)
_impl["VERIFIED_BASELINE"] = (
    _impl["HISTORY"] / "v186_to_c_top4_io_xp4_20260826_attempt013_xp_io"
    / "formal_incumbent_baseline/batch_result.json"
)


if __name__ == "__main__":
    raise SystemExit(int(_impl["main"]()))


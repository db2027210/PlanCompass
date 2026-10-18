#!/usr/bin/env python3
"""Launch v16 IO C from the verified four-lane cap-complete baseline."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PREDECESSOR = ROOT / "scripts/launch_mysql_algorithm_c_top4_io_xp4_recovery_v1.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
replacements = {
    'HISTORY / "v164_to_xp4_live_authority_20260826" / SCENE':
        'HISTORY / "v191_to_xp4_io_live_authority_refresh_20260826" / SCENE',
    'HISTORY / "v172_to_c_top4_io_xp4_recovery_20260826"':
        'HISTORY / "v192_to_c_top4_io_xp4_recovery_20260826"',
    '"v172_to_c_top4_io_xp4_20260826_attempt006_xp_io"':
        '"v192_to_c_top4_io_xp4_20260826_attempt014_xp_io"',
    'ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v1.py"':
        'ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v9.py"',
    '"mysql-algc-top4-io-xp4-v172.service"':
        '"mysql-algc-top4-io-xp4-v192.service"',
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"IO C launcher predecessor fragment differs: {old}")
    _source = _source.replace(old, new)
python_fragment = (
    '"/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,\n'
    '        str(PYTHON), str(CONTROLLER), "--run-id", RUN_ID,'
)
python_successor = (
    '"/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,\n'
    '        "/usr/bin/env", f"MYSQL_C_VERIFIED_BASELINE={VERIFIED_BASELINE}",\n'
    '        str(PYTHON), str(CONTROLLER), "--run-id", RUN_ID,'
)
if _source.count(python_fragment) != 1:
    raise RuntimeError("IO C launcher environment fragment differs")
_source = _source.replace(python_fragment, python_successor)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_algorithm_c_top4_io_xp4_recovery_v10_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)
_impl["VERIFIED_BASELINE"] = (
    _impl["HISTORY"]
    / "v186_to_c_top4_io_xp4_20260826_attempt013_xp_io"
    / "formal_incumbent_baseline/batch_result.json"
)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Counterbalanced A/B/C check bound to B v1132 and C v1133."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "scripts/compare_mysql_tpch_abc_client_qps_after_bc_v2.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
_replacements = {
    "v1129_to_b_tpch_client_qps_timeout_resume_20260830_tpch/run_state.json":
        "v1132_to_b_tpch_client_qps_frozen_outer2_resume_20260830_tpch/run_state.json",
    "v1130_to_c_tpch_client_qps_timeout_rows_20260830_tpch/run_state.json":
        "v1133_to_c_tpch_client_qps_timeout_rows_20260830_tpch/run_state.json",
    "mysql_tpch_abc_counterbalanced_client_qps_state_v2":
        "mysql_tpch_abc_counterbalanced_client_qps_state_v3",
    "mysql_tpch_abc_counterbalanced_launch_manifest_v2":
        "mysql_tpch_abc_counterbalanced_launch_manifest_v3",
}
for _old, _new in _replacements.items():
    if _source.count(_old) != 1:
        raise RuntimeError(f"comparison-v3 predecessor differs: {_old!r}")
    _source = _source.replace(_old, _new)

_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._compare_mysql_tpch_abc_client_qps_after_bc_v3_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

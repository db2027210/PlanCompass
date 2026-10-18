#!/usr/bin/env python3
"""Counterbalanced A/B/C check using the host-launched cached no-move fixed C."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "scripts/compare_mysql_tpch_abc_client_qps_after_bc_v5.py"
_source = PREDECESSOR.read_text(encoding="utf-8")


def _replace_once(old: str, new: str) -> None:
    global _source
    if _source.count(old) != 1:
        raise RuntimeError(f"comparison-v6 predecessor differs: {old!r}")
    _source = _source.replace(old, new)


_replace_once(
    "v1134_to_c_tpch_client_qps_scene_scoped_20260830_tpch/run_state.json",
    "v1151_to_c_tpch_cached_no_move_resume_20260830_tpch/run_state.json",
)
_replace_once(
    '"mysql_tpch_abc_counterbalanced_client_qps_v5"',
    '"mysql_tpch_abc_counterbalanced_client_qps_v7"',
)
_replace_once(
    '"mysql_tpch_abc_counterbalanced_client_qps_state_v5"',
    '"mysql_tpch_abc_counterbalanced_client_qps_state_v7"',
)
_replace_once(
    '"mysql_tpch_abc_counterbalanced_launch_manifest_v5"',
    '"mysql_tpch_abc_counterbalanced_launch_manifest_v7"',
)

_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._compare_mysql_tpch_abc_client_qps_after_bc_v7_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

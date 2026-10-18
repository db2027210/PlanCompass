#!/usr/bin/env python3
"""Run Algorithm C with the v42 independent-scene recovery corrections."""

from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v13 as base
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v42 as runtime


base.latest_runtime = runtime
base.CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260905_v105/"
    "algorithm_c_pg_latest_outer_contract.json"
)


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(base.CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    recovery = dict(contract.get("independent_scene_recovery") or {})
    boundary = dict(contract.get("algorithm_a_independent_check_boundary") or {})
    if (
        contract.get("release_version") != "v105-pg-latest-outer-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("local_real_replay_limit") != 10
        or flow.get("global_coverage_real_replay_limit") != 10
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("coverage_after_every_exhausted_local_round") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or recovery.get("standalone_relational_timeout_is_candidate_local") is not True
        or recovery.get("valid_sibling_retained") is not True
        or recovery.get("completed_outer_history_preserved") is not True
        or recovery.get("incomplete_outer_credit") != 0
        or recovery.get("resume_replay_archive_preserved") is not True
        or boundary.get("algorithm_a_feedback_to_c") is not False
    ):
        raise RuntimeError("Algorithm C v105 recovery contract differs")
    pg = dict(contract.get("postgresql_reference") or {})
    pg_path = Path(str(pg.get("path") or "")).resolve()
    if (
        not pg_path.is_file() or pg_path.is_symlink()
        or runtime.alg.sha(pg_path) != pg.get("sha256")
    ):
        raise RuntimeError("Algorithm C v105 PostgreSQL reference differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file() or path.is_symlink()
            or runtime.alg.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"Algorithm C v105 source differs: {name}: {path}")
    return contract


base.validate_contract = validate_contract


if __name__ == "__main__":
    raise SystemExit(base.main())

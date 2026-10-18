#!/usr/bin/env python3
"""Resume a two-worker XP scene with bounded PG wrapper recognition."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_all_scene_parallel_policy_v1 as parallel
from scripts import mysql_co_resident_pg_isolation_v14 as pg_wrapper
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as resume


_ORIGINAL_VALIDATE = resume.base.validate_fresh_authority
_ORIGINAL_FRESH_STATE = resume.base.fresh_state


def validate_fresh_authority(**kwargs):
    result = _ORIGINAL_VALIDATE(**kwargs)
    parallel.validate_contract()
    pg_wrapper.load_contract()
    pg_wrapper.install()
    return result


def fresh_state(**kwargs):
    state = parallel.annotate_state(_ORIGINAL_FRESH_STATE(**kwargs))
    state["pg_capture_wrapper_allowance"] = {
        "contract_path": str(pg_wrapper.CONTRACT),
        "contract_sha256": pg_wrapper.sha(pg_wrapper.CONTRACT),
        "rolling_dynamic_noninterference_gate_retained": True,
        "postgresql_process_control_or_stop_prohibited": True,
    }
    return state


def main() -> int:
    if "--scene" not in sys.argv:
        raise RuntimeError("XP scene is required")
    scene = sys.argv[sys.argv.index("--scene") + 1]
    if scene not in {"hybench_xp_cpu", "hybench_xp_io"}:
        raise RuntimeError("PG-wrapper successor is XP-only")
    parallel.validate_contract()
    pg_wrapper.load_contract()
    resume.base.validate_fresh_authority = validate_fresh_authority
    resume.base.fresh_state = fresh_state
    pg_wrapper.install()
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())

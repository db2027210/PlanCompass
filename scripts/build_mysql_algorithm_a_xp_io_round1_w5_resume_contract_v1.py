#!/usr/bin/env python3
"""Freeze the IO-A Round1 w5 recovered-resume transition."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_io_round1_w5_recovered_resume_v1 as resume


def main() -> int:
    output = resume.CONTRACT
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh contract required: {output}")
    bindings = {
        "controller": Path(resume.__file__).resolve(),
        "runtime_v12": Path(resume.runtime.__file__).resolve(),
        "source_state": resume.SOURCE_STATE,
        "source_baseline": resume.SOURCE_BASELINE,
        "source_candidates": resume.SOURCE_CANDIDATES,
        "recovered_binding": resume.RECOVERED_BINDING,
        "recovered_settle": resume.RECOVERED_SETTLE,
        "live_authority": resume.AUTHORITY,
    }
    resume.runtime.base.alg.atomic(output, {
        "schema_version": "mysql_algorithm_a_xp_io_round1_w5_resume_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": resume.SCENE,
        "baseline_physical_measurements_reused": True,
        "recovered_round1_reset_reused": True,
        "candidate_measurements_reused_false": True,
        "accepted_or_rejected_round_credit_zero": True,
        "candidate_set_unchanged": True,
        "resource_envelope_unchanged": True,
        "native_xp_workload_unchanged": True,
        "physical_replay_parallelism_eight": True,
        "external_process_control_or_stop_prohibited": True,
        "source_bindings": {
            label: {
                "path": str(path.relative_to(ROOT)),
                "sha256": resume.runtime.base.alg.sha(path),
            }
            for label, path in bindings.items()
        },
    })
    print({"status": "PASS", "contract": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

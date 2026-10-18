#!/usr/bin/env python3
"""Bind interval control and carry TPC-H normalization across formal R0 reuse."""

from __future__ import annotations

from scripts import mysql_algorithm_c_interval_control_v1 as interval_control
from scripts import mysql_algorithm_c_paper_baseline_gate_v1 as paper_baseline_gate
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v49 as v49

scene_checkpoint = v49.scene_checkpoint
_source = v49._source
_old = '''                "accepted_state_credit_increment": 0,
            }
        elif formal_partial is not None:
'''
_new = '''                "accepted_state_credit_increment": 0,
            }
            for normalization_name in (
                "same_worker_qps_normalization",
                "same_worker_qps_normalization_path",
                "same_worker_qps_normalization_sha256",
                "outer_acceptance_uses_same_worker_normalized_client_qps",
            ):
                if normalization_name in source_state:
                    state[normalization_name] = copy.deepcopy(
                        source_state[normalization_name]
                    )
        elif formal_partial is not None:
'''
if _source.count(_old) != 1:
    raise RuntimeError("Algorithm C v50 reused normalization boundary differs")
_source = _source.replace(_old, _new)
exec(compile(_source, str(__file__), "exec"), globals())

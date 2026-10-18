#!/usr/bin/env python3
"""Algorithm C successor requiring fresh physical evidence for acceptance."""

from __future__ import annotations

from pathlib import Path


PREDECESSOR_GENERATOR = Path(__file__).resolve().with_name(
    "run_mysql_four_scene_algorithm_c_boundary_resume_v39.py"
)
_source = PREDECESSOR_GENERATOR.read_text(encoding="utf-8")
_old = '''                    row["cached_valid_replay_redecision"] = True
                    row["semantic_batch_kind"] = "prephysical_cached_valid_replay"
                    cached_rows.append(row)
'''
_new = '''                    row["cached_valid_replay_redecision"] = False
                    row["cached_redecision_skipped_before_physical"] = True
                    row["cached_redecision_skip_reason"] = (
                        "stale_measurement_requires_current_outer_physical_replay"
                    )
                    state.setdefault(
                        "cached_stale_measurement_prephysical_exclusions", {}
                    ).setdefault(scene, 0)
                    state["cached_stale_measurement_prephysical_exclusions"][scene] += 1
                    continue
'''
if _source.count(_old) != 1:
    raise RuntimeError("Algorithm C v39 cached-redecision boundary differs")
_source = _source.replace(_old, _new)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

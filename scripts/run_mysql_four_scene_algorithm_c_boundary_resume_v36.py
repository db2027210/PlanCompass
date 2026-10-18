#!/usr/bin/env python3
"""Boundary C successor that filters stale cached no-move acceptances."""

from __future__ import annotations

from pathlib import Path


PREDECESSOR = (
    Path(__file__).resolve().with_name(
        "run_mysql_four_scene_algorithm_c_boundary_resume_v35.py"
    )
)
_source = PREDECESSOR.read_text(encoding="utf-8")
_old = '''                    row["cached_valid_replay_redecision"] = True
                    cached_rows.append(row)
'''
_new = '''                    try:
                        pg_outer.moved_shrink_subset(
                            incumbent_params=params_by_scene[scene],
                            candidate_ranking=_mapping(
                                candidate, "cached movement candidate ranking",
                            ),
                            active_params=active_by_scene[scene],
                        )
                    except RuntimeError as error:
                        if str(error) != (
                            "accepted candidate has no moved declared parameter"
                        ):
                            raise
                        row["cached_valid_replay_redecision"] = False
                        row["cached_redecision_skipped_before_physical"] = True
                        row["cached_redecision_skip_reason"] = (
                            "no_moved_declared_parameter_relative_to_current_incumbent"
                        )
                        state.setdefault(
                            "cached_no_move_prephysical_exclusions", {}
                        ).setdefault(scene, 0)
                        state["cached_no_move_prephysical_exclusions"][scene] += 1
                        continue
                    row["cached_valid_replay_redecision"] = True
                    cached_rows.append(row)
'''
if _source.count(_old) != 1:
    raise RuntimeError("Algorithm C v35 cached-row boundary differs")
_source = _source.replace(_old, _new)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

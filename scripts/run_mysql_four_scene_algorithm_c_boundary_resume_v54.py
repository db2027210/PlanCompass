#!/usr/bin/env python3
"""PG-exact binary intervals and de-duplicated retained regions."""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_algorithm_c_interval_control_v2 as interval_control
from scripts import mysql_outer_rejection_cycle_v3 as rejection_cycle
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v53 as v53


scene_checkpoint = v53.scene_checkpoint
_source = v53._source


def _replace_once(old: str, new: str, label: str) -> None:
    global _source
    if _source.count(old) != 1:
        raise RuntimeError(f"Algorithm C v54 {label} boundary differs")
    _source = _source.replace(old, new)


_replace_once(
    "from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle",
    "from scripts import mysql_outer_rejection_cycle_v3 as rejection_cycle",
    "rejection cycle import",
)

_replace_once(
    '''                        state["algorithm_c_retained_region_queue"][scene] = remaining
                        ranges_by_scene[scene] = expanded''',
    '''                        state["algorithm_c_retained_region_queue"][scene] = remaining
                        visited_region_ids = state.setdefault(
                            "algorithm_c_visited_region_ids", {}
                        ).setdefault(scene, [])
                        selected_region_id = str(selected_region.get("id") or "")
                        if not selected_region_id:
                            raise RuntimeError("selected retained region lacks identity")
                        if selected_region_id not in visited_region_ids:
                            visited_region_ids.append(selected_region_id)
                        ranges_by_scene[scene] = expanded''',
    "visited region accounting",
)

_replace_once(
    '''                        active_by_scene[scene],
                        resource_potential=resource_potential,
                    )
                    state.setdefault(
                        "algorithm_c_retained_region_queue", {}
                    ).setdefault(scene, []).extend(retained)''',
    '''                        active_by_scene[scene],
                        resource_potential=resource_potential,
                        created_outer_attempt=outer_attempt,
                    )
                    retained_queue = state.setdefault(
                        "algorithm_c_retained_region_queue", {}
                    ).setdefault(scene, [])
                    visited_region_ids = state.setdefault(
                        "algorithm_c_visited_region_ids", {}
                    ).setdefault(scene, [])
                    retained_queue, retained_region_audit = (
                        interval_control.append_new_regions(
                            retained_queue, visited_region_ids, retained,
                        )
                    )
                    state["algorithm_c_retained_region_queue"][scene] = retained_queue
                    state.setdefault(
                        "algorithm_c_retained_region_audit", {}
                    )[scene] = retained_region_audit''',
    "retained region de-duplication",
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

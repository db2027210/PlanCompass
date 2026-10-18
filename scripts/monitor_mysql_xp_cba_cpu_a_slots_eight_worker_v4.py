#!/usr/bin/env python3
"""Advance one XP scene C -> fresh B -> independent post-hoc A check on eight workers."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import repair_mysql_algorithm_c_scoped_terminal_status_v1 as repair


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xp_cba_cpu_a_slots_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_scene_v2.py":
        "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_eight_worker_scene_v1.py",
    "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_scene_v2.py":
        "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_eight_worker_scene_v1.py",
    "mysql_xp_cba_cpu_a_slots_independent_pipeline_v1":
        "mysql_xp_cba_cpu_a_slots_eight_worker_independent_pipeline_v4",
    "        c_state = load(args.c_state)\n":
        "        repair.repair_if_scoped_complete(args.c_state, scene)\n"
        "        c_state = load(args.c_state)\n",
    '    b_authority_root = pipeline / "b_live_authority"\n':
        '    b_authority_root = B_AUTHORITY_ROOTS[scene]\n',
    '                "--source-authority", str(args.source_authority),\n'
    '                "--run-id", args.a_run_id, "--pipeline-root", str(pipeline / "a_launch"),\n':
        '                "--run-id", args.a_run_id, "--pipeline-root", str(pipeline / "a_launch"),\n',
    '            "source_authority": str(args.source_authority),\n':
        '            "source_authority": str(args.source_authority),\n'
        '            "algorithm_a_role": "independent_posthoc_check_only",\n'
        '            "algorithm_a_feedback_to_b_or_c": False,\n'
        '            "algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c": False,\n',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"eight-worker CBA v4 predecessor differs: {old}")
    text = text.replace(old, new)

insertion = '''
B_AUTHORITY_ROOTS = {
    "hybench_xp_cpu": HISTORY / "v986_xp_cpu_eight_worker_cpu_a_slots_authority_20260828",
    "hybench_xp_io": HISTORY / "v987_xp_io_eight_worker_cpu_a_slots_authority_20260828",
}
'''
anchor = 'SCENE_SUFFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}\n'
if text.count(anchor) != 1:
    raise RuntimeError("eight-worker CBA v4 scene anchor differs")
text = text.replace(anchor, anchor + insertion)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xp_cba_cpu_a_slots_eight_worker_v4_impl",
    "__package__": "scripts",
    "repair": repair,
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

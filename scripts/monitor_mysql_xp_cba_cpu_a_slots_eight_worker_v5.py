#!/usr/bin/env python3
"""Advance C -> fresh XP8 B -> independent A check with terminal-time authority sealing."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import repair_mysql_algorithm_c_scoped_terminal_status_v1 as repair


SOURCE = Path(__file__).resolve().with_name("monitor_mysql_xp_cba_cpu_a_slots_v1.py")
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_scene_v2.py":
        "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_eight_worker_scene_v1.py",
    "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_scene_v2.py":
        "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_eight_worker_scene_v1.py",
    "mysql_xp_cba_cpu_a_slots_independent_pipeline_v1":
        "mysql_xp_cba_cpu_a_slots_eight_worker_independent_pipeline_v5",
    '    attempts: dict[str, list[dict[str, Any]]] = {"B": [], "A": []}\n':
        '    attempts: dict[str, list[dict[str, Any]]] = {"AUTHORITY": [], "B": [], "A": []}\n',
    '    next_attempt = {"B": 0.0, "A": 0.0}\n':
        '    next_attempt = {"AUTHORITY": 0.0, "B": 0.0, "A": 0.0}\n',
    "        c_state = load(args.c_state)\n":
        "        repair.repair_if_scoped_complete(args.c_state, scene)\n"
        "        c_state = load(args.c_state)\n",
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
        raise RuntimeError(f"eight-worker CBA v5 predecessor differs: {old}")
    text = text.replace(old, new)

anchor = 'SCENE_SUFFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}\n'
insertion = '''
AUTHORITY_BUILDER = ROOT / "scripts/build_mysql_xp8_live_authority_from_c_terminal_v1.py"
ADDITIONS = (
    HISTORY / "v1003_xp8_cpu_a_slots_additions_preprovision_20260828"
    / "additions_binding.json"
)
'''
if text.count(anchor) != 1:
    raise RuntimeError("eight-worker CBA v5 scene anchor differs")
text = text.replace(anchor, anchor + insertion)

launch_anchor = '''        if c_done and not b_state and current >= next_attempt["B"]:
            result = invoke([
'''
authority_block = '''        if (
            c_done and not b_state
            and not (b_authority_root / "expansion_manifest.json").is_file()
            and current >= next_attempt["AUTHORITY"]
        ):
            result = invoke([
                str(PYTHON), str(AUTHORITY_BUILDER), "--scene", scene,
                "--c-state", str(args.c_state), "--additions", str(ADDITIONS),
                "--output", str(b_authority_root),
            ])
            attempts["AUTHORITY"].append(result)
            next_attempt["AUTHORITY"] = time.monotonic() + 60.0
        if (
            c_done and not b_state
            and (b_authority_root / "expansion_manifest.json").is_file()
            and current >= next_attempt["B"]
        ):
            result = invoke([
'''
if text.count(launch_anchor) != 1:
    raise RuntimeError("eight-worker CBA v5 B launch anchor differs")
text = text.replace(launch_anchor, authority_block)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xp_cba_cpu_a_slots_eight_worker_v5_impl",
    "__package__": "scripts",
    "repair": repair,
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

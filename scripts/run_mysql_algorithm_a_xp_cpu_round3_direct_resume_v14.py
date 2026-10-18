#!/usr/bin/env python3
"""Resume CPU-A by directly reusing committed work through Round2."""

from pathlib import Path
import os
import traceback
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_cpu_round1_committed_resume_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v2 as reset8":
        "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v4 as reset8",
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6 as runtime":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v10 as runtime",
    "    waves = {index: verified(path) for index, path in SOURCE_WAVES.items()}\n":
        "    waves = {index: verified(path) for index, path in SOURCE_WAVES.items()}\n"
        "    round2_candidates = verified(SOURCE_ROUND2_CANDIDATES)\n"
        "    round2_wave1 = verified(SOURCE_ROUND2_WAVE1)\n"
        "    round2_wave2 = verified(SOURCE_ROUND2_WAVE2)\n"
        "    round2_rows1 = dict(round2_wave1.get(\"measurements\") or {})\n"
        "    round2_rows2 = dict(round2_wave2.get(\"measurements\") or {})\n",
    "    return {\n        \"baseline\": baseline_rows,\n":
        "    if (\n"
        "        int(round2_candidates.get(\"candidate_count\") or 0) != 16\n"
        "        or round2_wave1.get(\"status\") != \"PASS\"\n"
        "        or round2_wave2.get(\"status\") != \"PASS\"\n"
        "        or set(round2_rows1) != expected\n"
        "        or set(round2_rows2) != expected\n"
        "        or sorted(int(row.get(\"rank\") or 0) for row in round2_rows1.values())\n"
        "        != list(range(1, 9))\n"
        "        or sorted(int(row.get(\"rank\") or 0) for row in round2_rows2.values())\n"
        "        != list(range(9, 17))\n"
        "        or any(\n"
        "            row.get(\"hard_gate_passed\") is not True\n"
        "            or row.get(\"coverage_complete\") is not True\n"
        "            for row in [*round2_rows1.values(), *round2_rows2.values()]\n"
        "        )\n"
        "    ):\n"
        "        raise RuntimeError(\"CPU-A committed Round2 evidence differs\")\n"
        "    return {\n        \"baseline\": baseline_rows,\n",
    "        \"candidates\": candidates,\n    }\n":
        "        \"candidates\": candidates,\n"
        "        \"round2_candidates\": round2_candidates,\n"
        "        \"round2_wave1\": round2_rows1,\n"
        "        \"round2_wave2\": round2_rows2,\n"
        "    }\n",
    "            return physical_measure(**kwargs)\n":
        "            if (\n"
        "                output.name == \"attempt_01\"\n"
        "                and output.parent.name in {\"wave_001\", \"wave_002\"}\n"
        "                and output.parent.parent.name == \"round_002\"\n"
        "            ):\n"
        "                wave = int(output.parent.name.rsplit(\"_\", 1)[1])\n"
        "                role = f\"round_002_wave_{wave:03d}\"\n"
        "                if role not in reused_roles:\n"
        "                    current = runtime.base.alg.load(\n"
        "                        output.parent.parent / \"candidates.json\"\n"
        "                    )\n"
        "                    current_vectors = [\n"
        "                        {\"rank\": int(row[\"rank\"]), \"optimized_params\": row[\"optimized_params\"]}\n"
        "                        for row in current.get(\"candidates\") or []\n"
        "                    ]\n"
        "                    source_vectors = [\n"
        "                        {\"rank\": int(row[\"rank\"]), \"optimized_params\": row[\"optimized_params\"]}\n"
        "                        for row in evidence[\"round2_candidates\"].get(\"candidates\") or []\n"
        "                    ]\n"
        "                    source_rows = evidence[f\"round2_wave{wave}\"]\n"
        "                    expected_assignments = {\n"
        "                        int(row[\"rank\"]): row[\"params\"]\n"
        "                        for row in source_rows.values()\n"
        "                    }\n"
        "                    actual_assignments = {\n"
        "                        int(row[\"rank\"]): row[\"params\"]\n"
        "                        for row in kwargs[\"assignments\"].values()\n"
        "                    }\n"
        "                    if current_vectors != source_vectors or actual_assignments != expected_assignments:\n"
        "                        raise RuntimeError(f\"CPU-A reused Round2 Wave{wave} vectors differ\")\n"
        "                    reused_roles.add(role)\n"
        "                    return mirror_batch(\n"
        "                        source=(SOURCE_ROUND2_WAVE1 if wave == 1 else SOURCE_ROUND2_WAVE2),\n"
        "                        destination=output, state_path=Path(kwargs[\"state_path\"]),\n"
        "                        state=kwargs[\"state\"], role=role,\n"
        "                    )\n"
        "            return physical_measure(**kwargs)\n",
    'required = {"baseline", "round_001_wave_001", "round_001_wave_002"}':
        'required = {"baseline", "round_001_wave_001", "round_001_wave_002", '
        '"round_002_wave_001", "round_002_wave_002"}',
    "CPU-A committed Round1 evidence was not fully reused":
        "CPU-A committed Round1/Round2 evidence was not fully reused",
    'if reset_reused or "round_002_wave_001_attempt_01" not in token:':
        'if reset_reused or "round_003_wave_001_attempt_01" not in token:',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A direct Round3 predecessor differs: {old[:120]!r}")
    text = text.replace(old, new)

constant_marker = "RECOVERED_AUTHORITY = (\n"
if text.count(constant_marker) != 1:
    raise RuntimeError("CPU-A direct Round3 constant marker differs")
source_constants = '''SOURCE_ROUND2_ROOT = (
    HISTORY / "v739_to_a_cpu_refreshed_contract_resume_20260827_xp_cpu"
)
SOURCE_ROUND2_CANDIDATES = SOURCE_ROUND2_ROOT / "round_002/candidates.json"
SOURCE_ROUND2_WAVE1 = SOURCE_ROUND2_ROOT / "round_002/wave_001/attempt_01/batch_result.json"
SOURCE_ROUND2_WAVE2 = SOURCE_ROUND2_ROOT / "round_002/wave_002/attempt_01/batch_result.json"

'''
text = text.replace(constant_marker, source_constants + constant_marker)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_cpu_round3_direct_v14_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
HISTORY = implementation["HISTORY"]
implementation["RECOVERED_ROUND2_RESET"] = (
    HISTORY / "v780_to_a_cpu_v779_partial_recovery_20260827"
    / "xp_resets/v779_to_a_cpu_nested_hook_repaired_resume_20260827_xp_cpu_"
      "round_002_wave_002_attempt_01/reset_binding.json"
)
implementation["RECOVERED_AUTHORITY"] = (
    HISTORY / "v781_to_a_cpu_v779_recovered_authority_20260827/live_authority.json"
)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    try:
        raise SystemExit(implementation["main"]())
    except BaseException:
        diagnostic = os.environ.get("MYSQL_XP_CPU_A_FAILURE_DIAGNOSTIC", "")
        if diagnostic:
            Path(diagnostic).write_text(traceback.format_exc(), encoding="utf-8")
        raise

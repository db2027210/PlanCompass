#!/usr/bin/env python3
"""Resume CPU-A v9 after committed Round1 and valid Round2 Wave1."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_cpu_round1_committed_resume_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v2 as reset8":
        "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v4 as reset8",
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6 as runtime":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v9 as runtime",
    "    waves = {index: verified(path) for index, path in SOURCE_WAVES.items()}\n":
        "    waves = {index: verified(path) for index, path in SOURCE_WAVES.items()}\n"
        "    round2_candidates = verified(SOURCE_ROUND2_CANDIDATES)\n"
        "    round2_wave1 = verified(SOURCE_ROUND2_WAVE1)\n"
        "    round2_rows = dict(round2_wave1.get(\"measurements\") or {})\n",
    "    return {\n        \"baseline\": baseline_rows,\n":
        "    if (\n"
        "        int(round2_candidates.get(\"candidate_count\") or 0) != 16\n"
        "        or round2_wave1.get(\"status\") != \"PASS\"\n"
        "        or set(round2_rows) != expected\n"
        "        or sorted(int(row.get(\"rank\") or 0) for row in round2_rows.values())\n"
        "        != list(range(1, 9))\n"
        "        or any(\n"
        "            row.get(\"hard_gate_passed\") is not True\n"
        "            or row.get(\"coverage_complete\") is not True\n"
        "            for row in round2_rows.values()\n"
        "        )\n"
        "    ):\n"
        "        raise RuntimeError(\"CPU-A committed Round2 Wave1 differs\")\n"
        "    return {\n        \"baseline\": baseline_rows,\n",
    "        \"candidates\": candidates,\n    }\n":
        "        \"candidates\": candidates,\n"
        "        \"round2_candidates\": round2_candidates,\n"
        "        \"round2_wave1\": round2_rows,\n"
        "    }\n",
    "            return physical_measure(**kwargs)\n":
        "            if (\n"
        "                output.name == \"attempt_01\"\n"
        "                and output.parent.name == \"wave_001\"\n"
        "                and output.parent.parent.name == \"round_002\"\n"
        "                and \"round_002_wave_001\" not in reused_roles\n"
        "            ):\n"
        "                current = runtime.base.alg.load(\n"
        "                    output.parent.parent / \"candidates.json\"\n"
        "                )\n"
        "                current_vectors = [\n"
        "                    {\"rank\": int(row[\"rank\"]), \"optimized_params\": row[\"optimized_params\"]}\n"
        "                    for row in current.get(\"candidates\") or []\n"
        "                ]\n"
        "                source_vectors = [\n"
        "                    {\"rank\": int(row[\"rank\"]), \"optimized_params\": row[\"optimized_params\"]}\n"
        "                    for row in evidence[\"round2_candidates\"].get(\"candidates\") or []\n"
        "                ]\n"
        "                expected_assignments = {\n"
        "                    int(row[\"rank\"]): row[\"params\"]\n"
        "                    for row in evidence[\"round2_wave1\"].values()\n"
        "                }\n"
        "                actual_assignments = {\n"
        "                    int(row[\"rank\"]): row[\"params\"]\n"
        "                    for row in kwargs[\"assignments\"].values()\n"
        "                }\n"
        "                if current_vectors != source_vectors or actual_assignments != expected_assignments:\n"
        "                    raise RuntimeError(\"CPU-A reused Round2 Wave1 vectors differ\")\n"
        "                reused_roles.add(\"round_002_wave_001\")\n"
        "                return mirror_batch(\n"
        "                    source=SOURCE_ROUND2_WAVE1, destination=output,\n"
        "                    state_path=Path(kwargs[\"state_path\"]), state=kwargs[\"state\"],\n"
        "                    role=\"round_002_wave_001\",\n"
        "                )\n"
        "            return physical_measure(**kwargs)\n",
    'required = {"baseline", "round_001_wave_001", "round_001_wave_002"}':
        'required = {"baseline", "round_001_wave_001", "round_001_wave_002", "round_002_wave_001"}',
    "CPU-A committed Round1 evidence was not fully reused":
        "CPU-A committed Round1/Round2-Wave1 evidence was not fully reused",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A Round2 continuation predecessor differs: {old[:100]!r}")
    text = text.replace(old, new)

# The new live reset is created by the unlocked authority recovery before this
# controller starts.  Reusing it for the mirrored Wave1 adds no measurement or
# accepted/rejected credit.
constant_marker = "RECOVERED_AUTHORITY = (\n"
if text.count(constant_marker) != 1:
    raise RuntimeError("CPU-A continuation constant marker differs")
source_constants = '''SOURCE_ROUND2_ROOT = (
    HISTORY / "v592_to_a_cpu_top4_mixed_eight_worker_v6_round1_resume_20260827_xp_cpu"
)
SOURCE_ROUND2_CANDIDATES = SOURCE_ROUND2_ROOT / "round_002/candidates.json"
SOURCE_ROUND2_WAVE1 = (
    SOURCE_ROUND2_ROOT / "round_002/wave_001/attempt_01/batch_result.json"
)

'''
text = text.replace(constant_marker, source_constants + constant_marker)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_cpu_round2_wave1_resume_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
HISTORY = implementation["HISTORY"]
implementation["RECOVERED_ROUND2_RESET"] = (
    HISTORY / "v661_to_a_cpu_round2_merged_partial_long_storage_resume_20260827"
    / "xp_resets/algorithm_a_round_002_wave_002_unlocked_authority_v2/reset_binding.json"
)
implementation["RECOVERED_AUTHORITY"] = (
    HISTORY / "v662_to_a_cpu_merged_partial_long_storage_authority_20260827/live_authority.json"
)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

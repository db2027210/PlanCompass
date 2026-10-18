#!/usr/bin/env python3
"""Resume CPU-A at Round5 with committed R1-R4 and control2 reset."""

from pathlib import Path
import os
import sys
import traceback
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_cpu_round3_committed_resume_v17 as predecessor
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6 as reset8_v6


text = predecessor.text
replacements = {
    'SOURCE_ROUND3_WAVE2 = SOURCE_ROUND3_ROOT / "round_003/wave_002/attempt_01/batch_result.json"\n':
        'SOURCE_ROUND3_WAVE2 = SOURCE_ROUND3_ROOT / "round_003/wave_002/attempt_01/batch_result.json"\n'
        'SOURCE_ROUND4_ROOT = HISTORY / "v831_to_a_cpu_round4_resilient_resume_20260827_xp_cpu"\n'
        'SOURCE_ROUND4_CANDIDATES = SOURCE_ROUND4_ROOT / "round_004/candidates.json"\n'
        'SOURCE_ROUND4_WAVE1 = SOURCE_ROUND4_ROOT / "round_004/wave_001/attempt_01/batch_result.json"\n'
        'SOURCE_ROUND4_WAVE2 = SOURCE_ROUND4_ROOT / "round_004/wave_002/attempt_01/batch_result.json"\n',
    '    round3_rows2 = dict(round3_wave2.get("measurements") or {})\n':
        '    round3_rows2 = dict(round3_wave2.get("measurements") or {})\n'
        '    round4_candidates = verified(SOURCE_ROUND4_CANDIDATES)\n'
        '    round4_wave1 = verified(SOURCE_ROUND4_WAVE1)\n'
        '    round4_wave2 = verified(SOURCE_ROUND4_WAVE2)\n'
        '    round4_rows1 = dict(round4_wave1.get("measurements") or {})\n'
        '    round4_rows2 = dict(round4_wave2.get("measurements") or {})\n',
    '        raise RuntimeError("CPU-A committed Round3 evidence differs")\n    return {\n':
        '        raise RuntimeError("CPU-A committed Round3 evidence differs")\n'
        '    if (\n'
        '        int(round4_candidates.get("candidate_count") or 0) != 16\n'
        '        or round4_wave1.get("status") != "PASS"\n'
        '        or round4_wave2.get("status") != "PASS"\n'
        '        or set(round4_rows1) != expected\n'
        '        or set(round4_rows2) != expected\n'
        '        or sorted(int(row.get("rank") or 0) for row in round4_rows1.values())\n'
        '        != list(range(1, 9))\n'
        '        or sorted(int(row.get("rank") or 0) for row in round4_rows2.values())\n'
        '        != list(range(9, 17))\n'
        '        or any(\n'
        '            row.get("hard_gate_passed") is not True\n'
        '            or row.get("coverage_complete") is not True\n'
        '            for row in [*round4_rows1.values(), *round4_rows2.values()]\n'
        '        )\n'
        '    ):\n'
        '        raise RuntimeError("CPU-A committed Round4 evidence differs")\n'
        '    return {\n',
    '        "round3_wave2": round3_rows2,\n':
        '        "round3_wave2": round3_rows2,\n'
        '        "round4_candidates": round4_candidates,\n'
        '        "round4_wave1": round4_rows1,\n'
        '        "round4_wave2": round4_rows2,\n',
    '                    state=kwargs["state"], role=role,\n'
    '                )\n'
    '            return physical_measure(**kwargs)\n':
        '                    state=kwargs["state"], role=role,\n'
        '                )\n'
        '            if (\n'
        '                output.name == "attempt_01"\n'
        '                and output.parent.name in {"wave_001", "wave_002"}\n'
        '                and output.parent.parent.name == "round_004"\n'
        '                and f"round_004_wave_{int(output.parent.name[-3:]):03d}" not in reused_roles\n'
        '            ):\n'
        '                current = runtime.base.alg.load(\n'
        '                    output.parent.parent / "candidates.json"\n'
        '                )\n'
        '                current_vectors = [\n'
        '                    {"rank": int(row["rank"]), "optimized_params": row["optimized_params"]}\n'
        '                    for row in current.get("candidates") or []\n'
        '                ]\n'
        '                source_vectors = [\n'
        '                    {"rank": int(row["rank"]), "optimized_params": row["optimized_params"]}\n'
        '                    for row in evidence["round4_candidates"].get("candidates") or []\n'
        '                ]\n'
        '                wave = int(output.parent.name[-3:])\n'
        '                role = f"round_004_wave_{wave:03d}"\n'
        '                expected_assignments = {\n'
        '                    int(row["rank"]): row["params"]\n'
        '                    for row in evidence[f"round4_wave{wave}"].values()\n'
        '                }\n'
        '                actual_assignments = {\n'
        '                    int(row["rank"]): row["params"]\n'
        '                    for row in kwargs["assignments"].values()\n'
        '                }\n'
        '                if current_vectors != source_vectors or actual_assignments != expected_assignments:\n'
        '                    raise RuntimeError(f"CPU-A reused Round4 Wave{wave} vectors differ")\n'
        '                reused_roles.add(role)\n'
        '                return mirror_batch(\n'
        '                    source=(SOURCE_ROUND4_WAVE1 if wave == 1 else SOURCE_ROUND4_WAVE2),\n'
        '                    destination=output, state_path=Path(kwargs["state_path"]),\n'
        '                    state=kwargs["state"], role=role,\n'
        '                )\n'
        '            return physical_measure(**kwargs)\n',
    '        required = {"baseline", "round_001_wave_001", "round_001_wave_002", "round_002_wave_001", "round_002_wave_002", "round_003_wave_001", "round_003_wave_002"}\n':
        '        required = {"baseline", "round_001_wave_001", "round_001_wave_002", "round_002_wave_001", "round_002_wave_002", "round_003_wave_001", "round_003_wave_002", "round_004_wave_001", "round_004_wave_002"}\n',
    'CPU-A committed Round1/Round2/Round3 evidence was not fully reused':
        'CPU-A committed Round1/Round2/Round3/Round4 evidence was not fully reused',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A Round4-complete predecessor differs: {old[:120]!r} count={text.count(old)}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_cpu_round4_complete_v19_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
history = implementation["HISTORY"]
implementation["RECOVERED_ROUND2_RESET"] = (
    history / "v842_to_a_cpu_round5_control2_identity_rebind_20260827"
    / "rebound_reset_binding.json"
)
implementation["RECOVERED_AUTHORITY"] = (
    history / "v842_to_a_cpu_round5_control2_identity_rebind_20260827"
    / "authority/live_authority.json"
)
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v12"
)


def install_successor(value: Any, seen: set[int] | None = None) -> None:
    seen = seen or set()
    if id(value) in seen or not isinstance(value, dict):
        return
    seen.add(id(value))
    if "reset8" in value:
        value["reset8"] = reset8_v6
    if "CONTRACT_ROOT" in value:
        value["CONTRACT_ROOT"] = CONTRACT_ROOT
    if "CONTRACT" in value:
        value["CONTRACT"] = CONTRACT_ROOT / "algorithm_a_top4_contract.json"
    if "COORDINATION" in value:
        value["COORDINATION"] = CONTRACT_ROOT / "coordination_contract.json"
    for child in value.values():
        if isinstance(child, dict):
            install_successor(child, seen)


install_successor(implementation)
install_successor(implementation["runtime"].implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    try:
        raise SystemExit(implementation["main"]())
    except BaseException:
        diagnostic = os.environ.get("MYSQL_XP_CPU_A_FAILURE_DIAGNOSTIC", "")
        if diagnostic:
            Path(diagnostic).parent.mkdir(parents=True, exist_ok=True)
            Path(diagnostic).write_text(traceback.format_exc(), encoding="utf-8")
        raise

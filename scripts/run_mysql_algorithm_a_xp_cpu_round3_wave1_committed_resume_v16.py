#!/usr/bin/env python3
"""Resume CPU-A with Round3 Wave1 committed and Wave2 reset recovered."""

from pathlib import Path
import os
import sys
import traceback
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_cpu_round3_direct_resume_v14 as predecessor


text = predecessor.text
replacements = {
    '    round2_rows2 = dict(round2_wave2.get("measurements") or {})\n':
        '    round2_rows2 = dict(round2_wave2.get("measurements") or {})\n'
        '    round3_candidates = verified(SOURCE_ROUND3_CANDIDATES)\n'
        '    round3_wave1 = verified(SOURCE_ROUND3_WAVE1)\n'
        '    round3_rows1 = dict(round3_wave1.get("measurements") or {})\n',
    '        raise RuntimeError("CPU-A committed Round2 evidence differs")\n    return {\n':
        '        raise RuntimeError("CPU-A committed Round2 evidence differs")\n'
        '    if (\n'
        '        int(round3_candidates.get("candidate_count") or 0) != 16\n'
        '        or round3_wave1.get("status") != "PASS"\n'
        '        or set(round3_rows1) != expected\n'
        '        or sorted(int(row.get("rank") or 0) for row in round3_rows1.values())\n'
        '        != list(range(1, 9))\n'
        '        or any(\n'
        '            row.get("hard_gate_passed") is not True\n'
        '            or row.get("coverage_complete") is not True\n'
        '            for row in round3_rows1.values()\n'
        '        )\n'
        '    ):\n'
        '        raise RuntimeError("CPU-A committed Round3 Wave1 evidence differs")\n'
        '    return {\n',
    '        "round2_wave2": round2_rows2,\n':
        '        "round2_wave2": round2_rows2,\n'
        '        "round3_candidates": round3_candidates,\n'
        '        "round3_wave1": round3_rows1,\n',
    '            return physical_measure(**kwargs)\n':
        '            if (\n'
        '                output.name == "attempt_01"\n'
        '                and output.parent.name == "wave_001"\n'
        '                and output.parent.parent.name == "round_003"\n'
        '                and "round_003_wave_001" not in reused_roles\n'
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
        '                    for row in evidence["round3_candidates"].get("candidates") or []\n'
        '                ]\n'
        '                expected_assignments = {\n'
        '                    int(row["rank"]): row["params"]\n'
        '                    for row in evidence["round3_wave1"].values()\n'
        '                }\n'
        '                actual_assignments = {\n'
        '                    int(row["rank"]): row["params"]\n'
        '                    for row in kwargs["assignments"].values()\n'
        '                }\n'
        '                if current_vectors != source_vectors or actual_assignments != expected_assignments:\n'
        '                    raise RuntimeError("CPU-A reused Round3 Wave1 vectors differ")\n'
        '                reused_roles.add("round_003_wave_001")\n'
        '                return mirror_batch(\n'
        '                    source=SOURCE_ROUND3_WAVE1, destination=output,\n'
        '                    state_path=Path(kwargs["state_path"]), state=kwargs["state"],\n'
        '                    role="round_003_wave_001",\n'
        '                )\n'
        '            return physical_measure(**kwargs)\n',
    '        reset_reused = False\n':
        '        reset_reused_tokens: set[str] = set()\n',
    '            nonlocal reset_reused\n':
        '            nonlocal reset_reused_tokens\n',
    '            if reset_reused or "round_003_wave_001_attempt_01" not in token:\n':
        '            if token in reset_reused_tokens or not any(\n'
        '                marker in token for marker in (\n'
        '                    "round_003_wave_001_attempt_",\n'
        '                    "round_003_wave_002_attempt_",\n'
        '                )\n'
        '            ):\n',
    '            reset_reused = True\n':
        '            reset_reused_tokens.add(token)\n',
    '        required = {"baseline", "round_001_wave_001", "round_001_wave_002", "round_002_wave_001", "round_002_wave_002"}\n':
        '        required = {"baseline", "round_001_wave_001", "round_001_wave_002", "round_002_wave_001", "round_002_wave_002", "round_003_wave_001"}\n',
    'CPU-A committed Round1/Round2 evidence was not fully reused':
        'CPU-A committed Round1/Round2/Round3-Wave1 evidence was not fully reused',
    'SOURCE_ROUND2_WAVE2 = SOURCE_ROUND2_ROOT / "round_002/wave_002/attempt_01/batch_result.json"\n\n':
        'SOURCE_ROUND2_WAVE2 = SOURCE_ROUND2_ROOT / "round_002/wave_002/attempt_01/batch_result.json"\n'
        'SOURCE_ROUND3_ROOT = HISTORY / "v790_to_a_cpu_round3_recovered_resume_20260827_xp_cpu"\n'
        'SOURCE_ROUND3_CANDIDATES = SOURCE_ROUND3_ROOT / "round_003/candidates.json"\n'
        'SOURCE_ROUND3_WAVE1 = SOURCE_ROUND3_ROOT / "round_003/wave_001/attempt_01/batch_result.json"\n\n',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A Round3-Wave1 predecessor differs: {old[:100]!r}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_cpu_round3_wave1_v16_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
history = implementation["HISTORY"]
implementation["RECOVERED_ROUND2_RESET"] = (
    history / "v815_to_a_cpu_round3_wave2_w2_recovery_20260827"
    / "xp_resets/v790_to_a_cpu_round3_recovered_resume_20260827_xp_cpu_"
      "round_003_wave_002_attempt_01/reset_binding.json"
)
implementation["RECOVERED_AUTHORITY"] = (
    history / "v816_to_a_cpu_round3_wave2_w2_authority_20260827/live_authority.json"
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
            Path(diagnostic).parent.mkdir(parents=True, exist_ok=True)
            Path(diagnostic).write_text(traceback.format_exc(), encoding="utf-8")
        raise

#!/usr/bin/env python3
"""Resume CPU-A after both physical waves of Round3 were committed."""

from pathlib import Path
import os
import sys
import traceback
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_cpu_round3_wave1_committed_resume_v16 as predecessor


text = predecessor.text
replacements = {
    '    round3_wave1 = verified(SOURCE_ROUND3_WAVE1)\n'
    '    round3_rows1 = dict(round3_wave1.get("measurements") or {})\n':
        '    round3_wave1 = verified(SOURCE_ROUND3_WAVE1)\n'
        '    round3_wave2 = verified(SOURCE_ROUND3_WAVE2)\n'
        '    round3_rows1 = dict(round3_wave1.get("measurements") or {})\n'
        '    round3_rows2 = dict(round3_wave2.get("measurements") or {})\n',
    '        or round3_wave1.get("status") != "PASS"\n'
    '        or set(round3_rows1) != expected\n':
        '        or round3_wave1.get("status") != "PASS"\n'
        '        or round3_wave2.get("status") != "PASS"\n'
        '        or set(round3_rows1) != expected\n'
        '        or set(round3_rows2) != expected\n',
    '        != list(range(1, 9))\n'
    '        or any(\n'
    '            row.get("hard_gate_passed") is not True\n'
    '            or row.get("coverage_complete") is not True\n'
    '            for row in round3_rows1.values()\n'
    '        )\n'
    '    ):\n'
    '        raise RuntimeError("CPU-A committed Round3 Wave1 evidence differs")\n':
        '        != list(range(1, 9))\n'
        '        or sorted(int(row.get("rank") or 0) for row in round3_rows2.values())\n'
        '        != list(range(9, 17))\n'
        '        or any(\n'
        '            row.get("hard_gate_passed") is not True\n'
        '            or row.get("coverage_complete") is not True\n'
        '            for row in [*round3_rows1.values(), *round3_rows2.values()]\n'
        '        )\n'
        '    ):\n'
        '        raise RuntimeError("CPU-A committed Round3 evidence differs")\n',
    '        "round3_wave1": round3_rows1,\n':
        '        "round3_wave1": round3_rows1,\n'
        '        "round3_wave2": round3_rows2,\n',
    '                and output.parent.name == "wave_001"\n'
    '                and output.parent.parent.name == "round_003"\n'
    '                and "round_003_wave_001" not in reused_roles\n':
        '                and output.parent.name in {"wave_001", "wave_002"}\n'
        '                and output.parent.parent.name == "round_003"\n'
        '                and f"round_003_wave_{int(output.parent.name[-3:]):03d}" not in reused_roles\n',
    '                expected_assignments = {\n'
    '                    int(row["rank"]): row["params"]\n'
    '                    for row in evidence["round3_wave1"].values()\n'
    '                }\n':
        '                wave = int(output.parent.name[-3:])\n'
        '                role = f"round_003_wave_{wave:03d}"\n'
        '                expected_assignments = {\n'
        '                    int(row["rank"]): row["params"]\n'
        '                    for row in evidence[f"round3_wave{wave}"].values()\n'
        '                }\n',
    '                    raise RuntimeError("CPU-A reused Round3 Wave1 vectors differ")\n'
    '                reused_roles.add("round_003_wave_001")\n'
    '                return mirror_batch(\n'
    '                    source=SOURCE_ROUND3_WAVE1, destination=output,\n'
    '                    state_path=Path(kwargs["state_path"]), state=kwargs["state"],\n'
    '                    role="round_003_wave_001",\n'
    '                )\n':
        '                    raise RuntimeError(f"CPU-A reused Round3 Wave{wave} vectors differ")\n'
        '                reused_roles.add(role)\n'
        '                return mirror_batch(\n'
        '                    source=(SOURCE_ROUND3_WAVE1 if wave == 1 else SOURCE_ROUND3_WAVE2),\n'
        '                    destination=output, state_path=Path(kwargs["state_path"]),\n'
        '                    state=kwargs["state"], role=role,\n'
        '                )\n',
    '"round_003_wave_001"}\n':
        '"round_003_wave_001", "round_003_wave_002"}\n',
    'CPU-A committed Round1/Round2/Round3-Wave1 evidence was not fully reused':
        'CPU-A committed Round1/Round2/Round3 evidence was not fully reused',
    'SOURCE_ROUND3_ROOT = HISTORY / "v790_to_a_cpu_round3_recovered_resume_20260827_xp_cpu"\n':
        'SOURCE_ROUND3_ROOT = HISTORY / "v818_to_a_cpu_round3_wave1_committed_resume_20260827_xp_cpu"\n',
    'SOURCE_ROUND3_WAVE1 = SOURCE_ROUND3_ROOT / "round_003/wave_001/attempt_01/batch_result.json"\n\n':
        'SOURCE_ROUND3_WAVE1 = SOURCE_ROUND3_ROOT / "round_003/wave_001/attempt_01/batch_result.json"\n'
        'SOURCE_ROUND3_WAVE2 = SOURCE_ROUND3_ROOT / "round_003/wave_002/attempt_01/batch_result.json"\n\n',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A Round3-complete predecessor differs: {old[:120]!r}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_cpu_round3_complete_v17_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
history = implementation["HISTORY"]
implementation["RECOVERED_ROUND2_RESET"] = (
    history / "v824_to_a_cpu_current_identity_rebind_20260827"
    / "rebound_reset_binding.json"
)
implementation["RECOVERED_AUTHORITY"] = (
    history / "v824_to_a_cpu_current_identity_rebind_20260827"
    / "authority/live_authority.json"
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

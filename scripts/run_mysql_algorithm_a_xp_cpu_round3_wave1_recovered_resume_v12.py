#!/usr/bin/env python3
"""CPU-A continuation that installs technical reuse after runtime dispatch setup."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_cpu_round3_wave1_recovered_resume_v11.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "v759_to_a_cpu_round3_wave1_partial_recovery_20260827":
        "v771_to_a_cpu_v761_final_two_recovery_20260827",
    "v760_to_a_cpu_round3_wave1_recovered_authority_20260827":
        "v772_to_a_cpu_v761_final_two_authority_20260827",
    '"v739_to_a_cpu_refreshed_contract_resume_20260827_xp_cpu_"\n'
    '      "round_003_wave_001_attempt_01"':
        '"v761_to_a_cpu_round3_recovered_resume_20260827_xp_cpu_"\n'
        '      "round_002_wave_002_attempt_01"',
    "    original_measure = runtime.base.measure\n"
    "    original_reset = mixed_settle.reset_xp\n":
        "    original_measure = runtime.base.measure\n"
        "    original_reset = mixed_settle.reset_xp\n"
        "    installed_measure = original_measure\n"
        "    installed_reset = original_reset\n",
    "        return original_measure(**kwargs)\n":
        "        return installed_measure(**kwargs)\n",
    "            original_reset(freeze, workers, output, token, worker_ids=worker_ids)\n":
        "            installed_reset(freeze, workers, output, token, worker_ids=worker_ids)\n",
    "    runtime.base.measure = measure_with_round2_wave2_reuse\n"
    "    mixed_settle.reset_xp = reset_with_round3_recovery\n"
    "    try:\n"
    "        result = int(impl[\"main\"]())\n"
    "    finally:\n"
    "        runtime.base.measure = original_measure\n"
    "        mixed_settle.reset_xp = original_reset\n":
        "    original_install = runtime.install\n"
        "\n"
        "    def install_with_continuation() -> None:\n"
        "        nonlocal installed_measure, installed_reset\n"
        "        original_install()\n"
        "        installed_measure = runtime.base.measure\n"
        "        installed_reset = mixed_settle.reset_xp\n"
        "        runtime.base.measure = measure_with_round2_wave2_reuse\n"
        "        mixed_settle.reset_xp = reset_with_round3_recovery\n"
        "\n"
        "    runtime.install = install_with_continuation\n"
        "    try:\n"
        "        result = int(impl[\"main\"]())\n"
        "    finally:\n"
        "        runtime.install = original_install\n"
        "        runtime.base.measure = original_measure\n"
        "        mixed_settle.reset_xp = original_reset\n",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A v12 predecessor differs: {old[:120]!r}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_cpu_round3_resume_v12_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

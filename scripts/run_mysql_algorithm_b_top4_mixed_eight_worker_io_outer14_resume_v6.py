#!/usr/bin/env python3
"""Resume IO-B after committed Outer14 and reuse recovered Outer15 reset."""

from pathlib import Path
import os
import subprocess
import sys
import traceback
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE_SCRIPT = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_top4_mixed_eight_worker_io_outer12_resume_v4.py"
)
text = SOURCE_SCRIPT.read_text(encoding="utf-8")
replacements = {
    'SOURCE = HISTORY / "v735_to_b_io_outer11_entrypoint_repaired_resume_20260827_xp_io"':
        'SOURCE = HISTORY / "v775_to_b_io_outer14_drain_repaired_resume_20260827_xp_io"',
    'OUTER_11_ROOT = HISTORY / "v588_to_b_io_mixed_eight_worker_v7_outer10_resume_20260827_xp_io"':
        'OUTER_11_ROOT = HISTORY / "v588_to_b_io_mixed_eight_worker_v7_outer10_resume_20260827_xp_io"\n'
        'OUTER_12_ROOT = HISTORY / "v735_to_b_io_outer11_entrypoint_repaired_resume_20260827_xp_io"\n'
        'OUTER_13_ROOT = HISTORY / "v755_to_b_io_outer13_recovered_resume_20260827_xp_io"',
    'HISTORY / "v753_to_b_io_outer13_stale_task_repaired_20260827"':
        'HISTORY / "v798_to_b_io_outer15_w3_recovery_20260827"',
    '"xp_resets/algorithm_b_outer_013_rank_1/reset_binding.json"':
        '"xp_resets/algorithm_b_outer_015_rank_1/reset_binding.json"',
    'HISTORY / "v754_to_b_io_outer13_stale_task_repaired_authority_20260827"':
        'HISTORY / "v799_to_b_io_outer15_w3_authority_20260827"',
    '"rejected": 10,': '"rejected": 12,',
    '"outer": 12,': '"outer": 14,',
    '            12: SOURCE,':
        '            12: OUTER_12_ROOT,\n'
        '            13: OUTER_13_ROOT,\n'
        '            14: SOURCE,',
    'algorithm_b_outer_013_rank_1': 'algorithm_b_outer_015_rank_1',
    'Outer13': 'Outer15',
    'outer13': 'outer15',
    'committed_outer_reused_through": 12': 'committed_outer_reused_through": 14',
    'v755_to_b_io_outer15_recovered_resume_20260827_xp_io':
        'v755_to_b_io_outer13_recovered_resume_20260827_xp_io',
}
for old, new in replacements.items():
    if old not in text:
        raise RuntimeError(f"IO-B Outer15 predecessor differs: {old!r}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_io_outer14_v6_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
original_install_runtime = implementation["install_runtime"]


def install_runtime_with_slow_docker_inventory() -> Any:
    core = original_install_runtime()
    r0mod = core._impl["alg"].r0mod
    original_validate = r0mod._validate_authority

    def validate_with_slow_docker(*args: Any, **kwargs: Any) -> Any:
        original_run = subprocess.run

        def run_with_slow_docker(argv: Any, *run_args: Any, **run_kwargs: Any) -> Any:
            tokens = [str(value) for value in argv] if isinstance(argv, (list, tuple)) else []
            if tokens[:2] in (["docker", "inspect"], ["docker", "ps"]):
                run_kwargs["timeout"] = max(float(run_kwargs.get("timeout") or 0), 180.0)
            return original_run(argv, *run_args, **run_kwargs)

        subprocess.run = run_with_slow_docker
        try:
            return original_validate(*args, **kwargs)
        finally:
            subprocess.run = original_run

    r0mod._validate_authority = validate_with_slow_docker
    if hasattr(r0mod, "_impl"):
        r0mod._impl["_validate_authority"] = validate_with_slow_docker
    return core


implementation["install_runtime"] = install_runtime_with_slow_docker_inventory
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    try:
        raise SystemExit(implementation["main"]())
    except BaseException:
        diagnostic = os.environ.get("MYSQL_XP_IO_B_FAILURE_DIAGNOSTIC", "")
        if diagnostic:
            Path(diagnostic).parent.mkdir(parents=True, exist_ok=True)
            Path(diagnostic).write_text(traceback.format_exc(), encoding="utf-8")
        raise

#!/usr/bin/env python3
"""Resume XP/I/O Algorithm A from the failed Round-6 technical checkpoint.

Accepted rounds 1--4, the semantic rejection at round 5, the canonical
baseline, incumbent, ranges, and accepted history are retained.  No physical
candidate from the incomplete round 6 attempts is reused; all 16 Top4 vectors
are regenerated, compared with the frozen round-6 document, and replayed from
rank 1 on the current eight-worker runtime.
"""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_io_round4_checkpoint_resume_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")

# Apply structural naming changes before binding the new predecessor run,
# whose run id intentionally still records that it originally resumed round 4.
structural = {
    "Round-4": "Round-6",
    "Round 4": "Round 6",
    "Rounds 1--3": "Rounds 1--5",
    "ROUND4_": "ROUND6_",
    "round4_": "round6_",
    "round_004": "round_006",
    "rounds_1_to_3_retained": "rounds_1_to_5_history_retained",
}
for old, new in structural.items():
    if old not in text:
        raise RuntimeError(f"XP/I/O Round-6 structural predecessor differs: {old}")
    text = text.replace(old, new)

replacements = {
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v14",
    "v859_to_a_io_round2_wave2_physical_adapter_fixed_resume_20260828_xp_io":
        "v1056_to_a_io_round4_v14_resume_20260828_xp_io",
    "runtime_v12": "runtime_v14",
    "RESUME_FROM_VALID_ROUND6_AFTER_C_TERMINAL":
        "RESUME_FROM_VALID_ROUND4_AFTER_C_TERMINAL",
    'int(state.get("round_index") or 0) != 4':
        'int(state.get("round_index") or 0) != 6',
    'int(state.get("accepted") or 0) != 3':
        'int(state.get("accepted") or 0) != 4',
    'int(state.get("rejected_rounds") or 0) != 0':
        'int(state.get("rejected_rounds") or 0) != 1',
    'int(state.get("consecutive_rejected_rounds") or 0) != 0':
        'int(state.get("consecutive_rejected_rounds") or 0) != 1',
    'int(checkpoint.get("round_index") or 0) != 4':
        'int(checkpoint.get("round_index") or 0) != 6',
    'int(checkpoint.get("accepted_index") or 0) != 3':
        'int(checkpoint.get("accepted_index") or 0) != 4',
    'int(candidates.get("round_index") or 0) != 4':
        'int(candidates.get("round_index") or 0) != 6',
    'int(candidates.get("accepted_index") or 0) != 3':
        'int(candidates.get("accepted_index") or 0) != 4',
    "len(accepted_history) != 3": "len(accepted_history) != 4",
    "!= [1, 2, 3]": "!= [1, 2, 3, 4]",
    '"accepted": 3,\n            "round_index": 3,\n'
    '            "rejected_rounds": 0,\n'
    '            "consecutive_rejected_rounds": 0,':
        '"accepted": 4,\n            "round_index": 5,\n'
        '            "rejected_rounds": 1,\n'
        '            "consecutive_rejected_rounds": 1,',
    '"stage": "ROUND6_CHECKPOINT_AUTHORITY_VALIDATED"':
        '"stage": "ROUND6_TECHNICAL_CHECKPOINT_AUTHORITY_VALIDATED"',
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"XP/I/O Round-6 checkpoint predecessor differs: {old!r} count={count}"
        )
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_io_round6_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

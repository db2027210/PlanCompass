#!/usr/bin/env python3
"""Offline historical regression for robust XP exact-regime ranking."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/"
    "bundle_fix_v27_robust_exact_regime_history"
)
ROOT = Path(
    "/home/anonymous/program/resource_adaptive/history/"
    "xp_final_algorithm_fresh10_v26_20260731T1935/batches"
)
SELECTION = ROOT / "cont052/selection.json"
REPLAY = ROOT / "cont052/replay_summary.json"
NOISE_BOUND = 0.026087741478906423


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object: {path}")
    return value


def main() -> int:
    sys.path.insert(0, str(BUNDLE))
    from build_xp_exact_plan_repeat_selection import (
        robust_observation_history,
        robust_regime_audit,
    )

    selection = load(SELECTION)
    replay = load(REPLAY)
    observations = {
        str(item["fingerprint_id"]): item for item in replay["successful"]
    }
    stable_source_ranks = []
    for candidate in selection["top5"]:
        identifier = candidate["three_level_fingerprint"]["id"]
        history = robust_observation_history(
            candidate,
            observations[identifier],
            REPLAY,
        )
        audit = robust_regime_audit(history, NOISE_BOUND)
        assert audit["observation_count"] == 2
        scores = [float(item["actual_score"]) for item in history]
        assert audit["predicted_score"] == sum(scores) / 2.0
        assert audit["latest_sample_overwrite_forbidden"] is True
        if audit["trusted_eligible_after_next_clean_replay"] is True:
            stable_source_ranks.append(
                int(candidate["exact_plan_regime_calibration"]["source_rank"])
            )
    assert stable_source_ranks == [2, 5], stable_source_ranks

    output = Path("/tmp/xp_v27_cont052_expected_shortfall.json")
    completed = subprocess.run(
        [
            sys.executable,
            str(BUNDLE / "build_xp_exact_plan_repeat_selection.py"),
            "--selection",
            str(SELECTION),
            "--replay",
            str(REPLAY),
            "--minimum-score-span",
            repr(NOISE_BOUND),
            "--output",
            str(output),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert completed.returncode != 0
    assert "insufficient robust exact plan regimes" in completed.stdout
    assert not output.exists()

    print("XP robust exact-regime history v27 regression passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

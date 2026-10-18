#!/usr/bin/env python3
"""Regression test for budgeted-lane handoff to four-lane cohort support."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import run_pg_algorithm_c_budgeted_support as budgeted_support
from shell import run_pg_algorithm_c_cohort_support_supervisor as support


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def main() -> int:
    budgeted_support.configure_budgeted_support()
    assert "budgeted_formal_finished_clean" in support.CLEAN_TERMINAL_PHASES

    with tempfile.TemporaryDirectory() as temporary:
        campaign = Path(temporary)
        for scene in support.SCENES:
            phase = (
                "budgeted_formal_finished_clean"
                if scene == "xp_io"
                else "budgeted_formal_replay"
            )
            write_json(
                campaign / scene / support.STATE_FILE_NAME,
                {
                    "phase": phase,
                    "formal_candidate_wave_sequence": 8 if scene == "xp_io" else 9,
                },
            )
        assert support.lane_clean_terminal(campaign, "xp_io") is True
        assert support.campaign_clean_terminal(campaign) is False

        cohort_id = "budgeted-terminal-regression"
        stage_dir = support.barrier_dir(cohort_id, 9)
        try:
            stage_dir.mkdir(parents=True, exist_ok=True)
            ready_rows = []
            for scene in ("job_memory", "tpch_memory", "xp_cpu"):
                row = {
                    "scene": scene,
                    "wave_sequence": 9,
                    "round_index": 7,
                    "batch_kind": "formal_search",
                    "wave_start": 0,
                    "pid": os.getpid(),
                }
                ready_rows.append(row)
                write_json(
                    stage_dir / f"{support.participant_key(scene, 9)}.ready",
                    row,
                )

            assert support.next_campaign_wave_sequence(campaign, cohort_id) == 9
            peer = support.select_support_physical_peer(campaign, ready_rows)
            assert peer is not None
            assert peer["scene"] in {"job_memory", "tpch_memory", "xp_cpu"}
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

    print(
        "PASS: budgeted formal clean terminals hand off to cohort support",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

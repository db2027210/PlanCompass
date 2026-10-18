#!/usr/bin/env python3
"""Isolated self-test for the common-domain campaign retry wrapper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "shell" / "run_pg_common_domain_r4_with_retries.sh"


FAKE_RUNNER = r'''#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", type=Path, required=True)
parser.add_argument("--seed")
parser.add_argument("--max-stage-attempts")
parser.add_argument("--maximum-baseline-drift-pct")
parser.add_argument("--gptuner-xp-io-authority")
parser.add_argument("--aqetuner-xp-io-authority")
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
failed_once = args.output_dir.with_name(args.output_dir.name + "_failed_attempt_01").exists()
if not failed_once:
    (args.output_dir / "state.json").write_text(
        json.dumps({"phase": "failed_closed"}) + "\n", encoding="utf-8"
    )
    raise SystemExit(1)
(args.output_dir / "authority_manifest.json").write_text(
    json.dumps({
        "contract": "pg_four_scene_adjacent_bracketed_frozen_champion_replay_v2",
        "phase": "complete",
    }) + "\n",
    encoding="utf-8",
)
'''

FAKE_CLEAN_STOP_RUNNER = r'''#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", type=Path, required=True)
parser.add_argument("--seed")
parser.add_argument("--max-stage-attempts")
parser.add_argument("--maximum-baseline-drift-pct")
parser.add_argument("--gptuner-xp-io-authority")
parser.add_argument("--aqetuner-xp-io-authority")
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
(args.output_dir / "state.json").write_text(
    json.dumps({"phase": "clean_stopped"}) + "\n", encoding="utf-8"
)
'''

FAKE_DRIFT_STOP_RUNNER = FAKE_CLEAN_STOP_RUNNER.replace(
    '"clean_stopped"', '"failed_closed_adjacent_baseline_drift"'
).replace("'''", "'''")


def stop_case(temp: Path, runner_source: str, expected_rc: int, receipt_name: str) -> None:
    fake_project = temp / "project"
    fake_shell = fake_project / "shell"
    fake_shell.mkdir(parents=True)
    fake_runner = fake_shell / "run_pg_common_domain_frozen_champions.py"
    fake_runner.write_text(runner_source, encoding="utf-8")
    final_root = temp / "formal_r4"
    gptuner = temp / "gptuner.json"
    aqetuner = temp / "aqetuner.json"
    gptuner.write_text("{}\n", encoding="utf-8")
    aqetuner.write_text("{}\n", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "PROJECT_ROOT": str(fake_project),
            "PYTHON_BIN": sys.executable,
            "PG_COMMON_DOMAIN_R4_ROOT": str(final_root),
            "PG_GPTUNER_XPIO_AUTHORITY": str(gptuner),
            "PG_AQETUNER_XPIO_AUTHORITY": str(aqetuner),
            "PG_COMMON_DOMAIN_MAX_CAMPAIGN_ATTEMPTS": "3",
        }
    )
    completed = subprocess.run(["bash", str(WRAPPER)], env=env, check=False)
    assert completed.returncode == expected_rc
    receipt = json.loads((final_root / receipt_name).read_text(encoding="utf-8"))
    assert receipt["automatic_retry_suppressed"] is True
    assert not final_root.with_name(final_root.name + "_failed_attempt_01").exists()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pg_r4_retry_test_") as raw_temp:
        temp = Path(raw_temp)
        fake_project = temp / "project"
        fake_shell = fake_project / "shell"
        fake_shell.mkdir(parents=True)
        fake_runner = fake_shell / "run_pg_common_domain_frozen_champions.py"
        fake_runner.write_text(FAKE_RUNNER, encoding="utf-8")
        final_root = temp / "formal_r4"
        gptuner = temp / "gptuner.json"
        aqetuner = temp / "aqetuner.json"
        gptuner.write_text("{}\n", encoding="utf-8")
        aqetuner.write_text("{}\n", encoding="utf-8")
        env = dict(os.environ)
        env.update(
            {
                "PROJECT_ROOT": str(fake_project),
                "PYTHON_BIN": sys.executable,
                "PG_COMMON_DOMAIN_R4_ROOT": str(final_root),
                "PG_GPTUNER_XPIO_AUTHORITY": str(gptuner),
                "PG_AQETUNER_XPIO_AUTHORITY": str(aqetuner),
                "PG_COMMON_DOMAIN_MAX_CAMPAIGN_ATTEMPTS": "3",
            }
        )
        subprocess.run(["bash", str(WRAPPER)], env=env, check=True)

        failed_root = final_root.with_name(final_root.name + "_failed_attempt_01")
        assert (failed_root / "campaign_failure_receipt.json").is_file()
        authority = json.loads((final_root / "authority_manifest.json").read_text(encoding="utf-8"))
        receipt = json.loads((final_root / "campaign_retry_receipt.json").read_text(encoding="utf-8"))
        assert authority["phase"] == "complete"
        assert receipt["successful_attempt"] == 2
        assert receipt["failed_attempts_preserved"] == 1
    with tempfile.TemporaryDirectory(prefix="pg_r4_clean_stop_test_") as raw_temp:
        stop_case(
            Path(raw_temp),
            FAKE_CLEAN_STOP_RUNNER,
            3,
            "campaign_clean_stop_receipt.json",
        )
    with tempfile.TemporaryDirectory(prefix="pg_r4_drift_stop_test_") as raw_temp:
        stop_case(
            Path(raw_temp),
            FAKE_DRIFT_STOP_RUNNER,
            4,
            "campaign_drift_stop_receipt.json",
        )
    print("common-domain r4 retry wrapper self-test passed", flush=True)


if __name__ == "__main__":
    main()

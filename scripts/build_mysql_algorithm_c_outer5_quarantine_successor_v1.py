#!/usr/bin/env python3
"""Rebind unchanged replay quarantine state to the completed outer-5 checkpoint."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "history/mysql_four_scene_20260809/"
    "algorithm_c_v18_replay_quarantine_v6_final3_20260813T1925p0800/"
    "replay_quarantine.json"
)
CHECKPOINT = (
    ROOT / "history/mysql_four_scene_20260809/"
    "algorithm_c_v18_outer5_checkpoint_v7_20260814T1745p0800/"
    "prepared_binding.json"
)
OUTPUT = (
    ROOT / "history/mysql_four_scene_20260809/"
    "algorithm_c_v18_replay_quarantine_v6_outer5_v7_20260814T1747p0800/"
    "replay_quarantine.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    value = copy.deepcopy(json.loads(SOURCE.read_text(encoding="utf-8")))
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    if (
        value.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v6"
        or checkpoint.get("status") != "PREPARED"
        or int(checkpoint.get("completed_outer_attempts") or 0) != 5
        or value.get("freeze_sha256") != checkpoint.get("freeze_sha256")
    ):
        raise RuntimeError("outer-5 quarantine successor authority differs")
    value["resume_checkpoint_outer_attempt"] = 5
    value["evidence"]["outer5_checkpoint"] = {
        "path": str(CHECKPOINT.resolve()), "sha256": sha(CHECKPOINT),
    }
    value["evidence"]["source_quarantine_outer4"] = {
        "path": str(SOURCE.resolve()), "sha256": sha(SOURCE),
    }
    value["builder_source_path"] = str(Path(__file__).resolve())
    value["builder_source_sha256"] = sha(Path(__file__).resolve())
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({"path": str(OUTPUT), "sha256": sha(OUTPUT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

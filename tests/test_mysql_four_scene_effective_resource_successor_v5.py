from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v5"
FREEZE = FREEZE_DIR / "four_scene_effective_resource_successor.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v5_freezes_page_aligned_effective_xp_io_memory() -> None:
    value = json.loads(FREEZE.read_text(encoding="utf-8"))
    assert value["schema_version"].endswith("effective_resource_successor_v5")
    assert value["formal_scope"] == "mysql_only"
    assert value["cross_engine_comparison_prohibited"] is True
    assert value["formal_launch_authorized"] is False
    workers = value["workers"]
    assert len(workers) == 8
    xp_io = [row for row in workers if row["scene"] == "hybench_xp_io"]
    assert len(xp_io) == 2
    for row in xp_io:
        assert row["memory_bytes"] == 6_871_945_216
        assert row["memory_bytes"] % row["memory_alignment_bytes"] == 0
        assert row["memory_request_predecessor_bytes"] == 6_871_947_674


def test_v5_binds_fail_closed_predecessor_and_checksums() -> None:
    value = json.loads(FREEZE.read_text(encoding="utf-8"))
    failure = ROOT / value["predecessor_data_plane_failure"]["path"]
    assert _sha(failure) == value["predecessor_data_plane_failure"]["sha256"]
    predecessor = ROOT / value["predecessor"]["path"]
    assert _sha(predecessor) == value["predecessor"]["sha256"]
    for line in (FREEZE_DIR / "checksums.sha256").read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        assert _sha(FREEZE_DIR / name) == expected

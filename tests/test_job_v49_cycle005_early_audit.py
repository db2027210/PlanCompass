from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/audit_job_v49_cycle005_early.py"


def import_module():
    spec = importlib.util.spec_from_file_location("job_cycle005_audit_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import early cycle auditor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_module = import_module()


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate(rank: int) -> dict:
    identifier = "three-level-sha256:" + f"{rank:064x}"
    return {
        "rank": rank,
        "selection_role": "probe",
        "boundary_corner": False,
        "three_level_fingerprint": {"id": identifier, "valid": True},
        **{field: {} for field in audit_module.REQUIRED_CANDIDATE_FIELDS},
    }


class JobV49Cycle005EarlyAuditTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        b = root / "attempt"
        t = b / audit_module.FORMAL_CAMPAIGN
        autonomous = t / audit_module.AUTONOMOUS_ROOT
        state_path = t / "package/formal_state.json"
        dump(
            state_path,
            {"accepted_count": 3, "global_ordinal": 386, "formal_replay_count": 206},
        )
        dump(
            autonomous / "controller_state.json",
            {
                "schema_version": "job_fresh10_target10_autonomous_v49",
                "status": "running",
                "cycle": 5,
                "phase": "successor_discovery",
                "accepted_count": 3,
                "target_accepted": 10,
                "formal_state_sha256": sha(state_path),
                "manual_candidate_injection": False,
                "qps_used_for_inner_scoring": False,
            },
        )
        for name, value in (
            ("replay_counter.txt", 386),
            ("formal_search_replay_counter.txt", 206),
            ("formal_search_accepted_counter.txt", 3),
        ):
            (b / name).write_text(f"{value}\n", encoding="utf-8")
        return b, autonomous, autonomous / "cycle_005", state_path

    def test_inventory_scan_is_valid_in_progress_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_cycle005_scan_") as raw:
            b, _autonomous, cycle, _state = self.fixture(Path(raw))
            cycle.mkdir(parents=True)
            result = audit_module.run(
                type("Args", (), {"attempt_root": b, "cycle": 5})()
            )
            self.assertEqual(result["status"], "pass_or_in_progress")
            self.assertEqual(
                result["detected_stage"], "local_inventory_or_incumbent_scan"
            )
            self.assertIn("global_exclusion.json not sealed yet", result["warnings"])

    def test_sealed_discovery_validates_inventory_determinism_and_audit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_cycle005_ready_") as raw:
            b, _autonomous, cycle, state_path = self.fixture(Path(raw))
            source = b / "historical_top5.json"
            old_identifier = "three-level-sha256:" + "f" * 64
            dump(source, {"three_level_fingerprint": {"id": old_identifier}})
            dump(
                cycle / "global_exclusion.json",
                {
                    "schema_version": "job_global_three_level_exclusion_v1",
                    "sources": [{"path": str(source), "sha256": sha(source)}],
                    "fingerprint_count": 1,
                    "fingerprints": [
                        {"three_level_fingerprint": {"id": old_identifier}}
                    ],
                },
            )
            selection = {
                "diagnostic_only": True,
                "top5": [candidate(rank) for rank in range(1, 6)],
            }
            dump(cycle / "discovery_top5.json", selection)
            (cycle / "discovery_top5.second.json").write_bytes(
                (cycle / "discovery_top5.json").read_bytes()
            )
            (cycle / "generator_a.log").write_text("ok\n", encoding="utf-8")
            (cycle / "generator_b.log").write_text("ok\n", encoding="utf-8")
            dump(
                cycle / "discovery_audit.json",
                {
                    "status": "five_globally_novel_noncorner_probe_fingerprints",
                    "selection_sha256": sha(cycle / "discovery_top5.json"),
                    "fingerprints": sorted(
                        item["three_level_fingerprint"]["id"]
                        for item in selection["top5"]
                    ),
                    "excluded_fingerprint_count_before": 1,
                    "new_workload_calls": 0,
                    "formal_state_sha256": sha(state_path),
                },
            )
            result = audit_module.run(
                type("Args", (), {"attempt_root": b, "cycle": 5})()
            )
            self.assertEqual(result["status"], "pass_or_in_progress")
            self.assertEqual(result["detected_stage"], "discovery_ready")
            self.assertFalse(result["errors"])

    def test_discovery_collision_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_cycle005_collision_") as raw:
            b, _autonomous, cycle, _state = self.fixture(Path(raw))
            source = b / "historical_top5.json"
            dump(source, {"ok": True})
            colliding = candidate(1)["three_level_fingerprint"]["id"]
            dump(
                cycle / "global_exclusion.json",
                {
                    "schema_version": "job_global_three_level_exclusion_v1",
                    "sources": [{"path": str(source), "sha256": sha(source)}],
                    "fingerprint_count": 1,
                    "fingerprints": [
                        {"three_level_fingerprint": {"id": colliding}}
                    ],
                },
            )
            selection = {
                "diagnostic_only": True,
                "top5": [candidate(rank) for rank in range(1, 6)],
            }
            dump(cycle / "discovery_top5.json", selection)
            (cycle / "discovery_top5.second.json").write_bytes(
                (cycle / "discovery_top5.json").read_bytes()
            )
            (cycle / "generator_a.log").write_text("ok\n", encoding="utf-8")
            (cycle / "generator_b.log").write_text("ok\n", encoding="utf-8")
            result = audit_module.run(
                type("Args", (), {"attempt_root": b, "cycle": 5})()
            )
            self.assertEqual(result["status"], "fail_closed")
            self.assertTrue(
                any("discovery_globally_novel" in error for error in result["errors"])
            )


if __name__ == "__main__":
    unittest.main()

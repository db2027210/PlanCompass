from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
V61 = TOOLS / "job_fresh10_target10_autonomous_v61.py"
LAUNCHER = TOOLS / "launch_job_target10_autonomous_v61.sh"
sys.path.insert(0, str(TOOLS))
import job_fresh10_target10_autonomous_v61 as v61  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fingerprint_ids() -> list[str]:
    return [f"three-level-sha256:{index:064x}" for index in range(20)]


def budget(fresh_root: Path, invocations: list[dict]) -> dict:
    now = time.time()
    return {
        "schema_version": v61.v55.SUCCESSOR_BUDGET_SCHEMA,
        "status": "generating",
        "replay_attempt": 5,
        "fresh_root": str(fresh_root.resolve()),
        "generator_slots": list(v61.RAW_SLOTS),
        "generator_invocation_limit": 2,
        "per_generator_timeout_seconds": 1800,
        "wall_timeout_seconds": 3720,
        "started_unix_seconds": now,
        "deadline_unix_seconds": now + 3720,
        "invocations": invocations,
        "workload_calls": 0,
        "formal_state_updates": 0,
    }


def install_authority(root: Path):
    v49 = v61.implementation.v49
    old = {
        "state_pin": v49.state_pin,
        "counter": v49.counter,
        "ensure": v61.v60.ensure_network_quarantine_v60,
    }
    v49.state_pin = lambda: ("b" * 64, {"accepted_count": 1})
    v49.counter = lambda path: {
        str(v49.GLOBAL_COUNTER): 424,
        str(v49.FORMAL_REPLAY_COUNTER): 211,
        str(v49.ACCEPTED_COUNTER): 1,
    }[str(path)]
    identifiers = fingerprint_ids()
    v61.v60.ensure_network_quarantine_v60 = lambda cycle: {
        "quarantined_fingerprints": identifiers
    }
    fresh = root / "cycle_002/fresh_discovery_05"
    write_json(
        fresh / "exclusion.json",
        {
            "schema_version": "job_v50_fresh_diagnostic_exclusion_v1",
            "fingerprint_count": len(identifiers),
            "fingerprints": [
                {"three_level_fingerprint": {"id": identifier}}
                for identifier in identifiers
            ],
            "sources": [],
        },
    )
    return fresh, old


def restore_authority(old: dict) -> None:
    v49 = v61.implementation.v49
    v49.state_pin = old["state_pin"]
    v49.counter = old["counter"]
    v61.v60.ensure_network_quarantine_v60 = old["ensure"]


def test_started_progress_is_quarantined_and_regenerated() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fresh, old = install_authority(root)
        try:
            write_json(
                fresh / "v55_successor_budget.json",
                budget(
                    fresh,
                    [
                        {
                            "ordinal": 1,
                            "slot": "historical_training_a.json",
                            "status": "started",
                            "timeout_seconds": 1800,
                            "log_path": str(fresh / "generator_a.log"),
                        }
                    ],
                ),
            )
            write_json(
                fresh / "historical_training_a.json.progress.json",
                {
                    "status": "explain_only_generation_in_progress",
                    "completed_anchor_count": 1,
                    "generated_probe_count": 1,
                    "generated_trusted_count": 1,
                    "last_anchor": 0,
                    "new_workload_calls": 0,
                },
            )
            authority_before = v61._authority_pin()
            audit = v61.recover_partial_successor_v61(root / "cycle_002")
            assert audit is not None
            assert audit["cursor_reusable"] is False
            assert audit["replacement_generator_invocation_limit"] == 2
            assert audit["new_workload_calls"] == 0
            assert not (fresh / "historical_training_a.json.progress.json").exists()
            assert (fresh / "partial_quarantine_v61/historical_training_a.json.progress.json").is_file()
            replacement = json.loads(
                (fresh / "v55_successor_budget.json").read_text(encoding="utf-8")
            )
            assert replacement["invocations"] == []
            assert replacement["recovery_epoch"] == v61.RECOVERY_EPOCH
            assert v61._authority_pin() == authority_before
            assert v61.recover_partial_successor_v61(root / "cycle_002") == audit
        finally:
            restore_authority(old)


def test_prepared_transaction_resumes_after_budget_move() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fresh, old = install_authority(root)
        try:
            write_json(
                fresh / "v55_successor_budget.json",
                budget(
                    fresh,
                    [{"ordinal": 1, "slot": "historical_training_a.json", "status": "started"}],
                ),
            )
            first = v61.recover_partial_successor_v61(root / "cycle_002")
            assert first is not None
            transaction_path = fresh / "v61_partial_recovery_transaction.json"
            transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
            transaction["status"] = "prepared"
            transaction.pop("prepared_transaction_sha256", None)
            transaction.pop("audit_sha256", None)
            write_json(transaction_path, transaction)
            (fresh / "v61_partial_recovery_audit.json").unlink()
            (fresh / "v61_replacement_budget_seed.json").unlink()
            resumed = v61.recover_partial_successor_v61(root / "cycle_002")
            assert resumed is not None and resumed["status"] == "committed"
            assert (fresh / "partial_quarantine_v61/v55_successor_budget.json").is_file()
        finally:
            restore_authority(old)


def test_completed_raw_slot_is_preserved() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fresh, old = install_authority(root)
        try:
            raw = fresh / "historical_training_a.json"
            write_json(raw, {"top5": []})
            write_json(
                fresh / "v55_successor_budget.json",
                budget(
                    fresh,
                    [
                        {
                            "ordinal": 1,
                            "slot": raw.name,
                            "status": "complete",
                            "artifact_sha256": sha(raw),
                        }
                    ],
                ),
            )
            audit = v61.recover_partial_successor_v61(root / "cycle_002")
            assert audit is not None
            assert raw.is_file()
            assert audit["preserved_complete_slots"] == [raw.name]
            replacement = json.loads(
                (fresh / "v55_successor_budget.json").read_text(encoding="utf-8")
            )
            assert len(replacement["invocations"]) == 1
            assert replacement["replacement_generator_invocation_limit"] == 1
        finally:
            restore_authority(old)


def test_unbudgeted_partial_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fresh, old = install_authority(root)
        try:
            write_json(fresh / "top5.json", {"partial": True})
            try:
                v61.recover_partial_successor_v61(root / "cycle_002")
            except v61.implementation.v49.FailClosed as error:
                assert "unbudgeted" in str(error)
            else:
                raise AssertionError("v61 accepted an unbudgeted partial output")
        finally:
            restore_authority(old)


def test_launcher_binds_partial_authority_and_zero_call_preflight() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert sha(V61) in source
    for required in (
        "7227bb14d41ad29e0dea76c031b20f69c34578988217998513fa70714b3cea22",
        "8ea0b00e3b0e4b84764ec75e242cc28fdba6f5cf7305d63f877983ad6787da4b",
        "job_v61_restart_safe_successor_partial_recovery_v1",
        ".replacement_generator_invocation_limit == 2",
        ".new_workload_calls == 0",
        'V61_PREFLIGHT_ONLY:-0',
        '.controller == "job_v61"',
    ):
        assert required in source, required


def main() -> int:
    test_started_progress_is_quarantined_and_regenerated()
    test_prepared_transaction_resumes_after_budget_move()
    test_completed_raw_slot_is_preserved()
    test_unbudgeted_partial_fails_closed()
    test_launcher_binds_partial_authority_and_zero_call_preflight()
    print("JOB v61 partial recovery tests passed: 5", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

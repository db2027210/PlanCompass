from pathlib import Path
import tempfile

import pytest

from formal_recost_artifacts import (
    RecostArtifactError,
    append_jsonl_fsync,
    create_attempt_directory,
    validate_attempt,
    validate_checksums,
    write_checksums,
    write_empty_new,
    write_new_bytes,
    write_new_json,
)


def build_attempt(root: Path, *, kind="candidate", status="complete"):
    attempt = create_attempt_directory(root, 0, 1)
    write_new_json(attempt / "round_manifest.json", {"kind": kind})
    append_jsonl_fsync(attempt / "events.jsonl", {"event": "round_start"})
    if kind == "candidate":
        append_jsonl_fsync(
            attempt / "inner_iterations.jsonl",
            {"label": "inner_step_accepted", "inner_step_accepted": False},
        )
    else:
        write_empty_new(attempt / "inner_iterations.jsonl")
    for name in (
        "params_before.json", "params_candidate.json", "params_applied.json",
        "plan.json", "plan_fingerprint.json", "benchmark_result.json",
        "stability.json", "decision.json",
    ):
        write_new_json(attempt / name, {"status": status})
    if status == "complete":
        append_jsonl_fsync(attempt / "resource_samples.jsonl", {"sample": 1})
        append_jsonl_fsync(attempt / "database_samples.jsonl", {"sample": 1})
    else:
        write_empty_new(attempt / "resource_samples.jsonl")
        write_empty_new(attempt / "database_samples.jsonl")
    write_empty_new(attempt / "throughput_windows.jsonl")
    append_jsonl_fsync(attempt / "events.jsonl", {"event": "round_end"})
    write_new_json(attempt / "round_summary.json", {"status": status})
    return attempt


def test_candidate_attempt_has_fixed_files_and_inner_outer_separation():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        attempt = build_attempt(root)
        result = validate_attempt(attempt)
        assert result["kind"] == "candidate"
        assert result["inner_iterations"] == 1


def test_failed_candidate_may_have_empty_samples_but_keeps_all_files():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        attempt = build_attempt(root, status="timeout")
        assert validate_attempt(attempt)["status"] == "timeout"


def test_checksum_detects_tamper_and_forbids_overwrite():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_new_json(root / "run_manifest.json", {"status": "complete"})
        result = write_checksums(root)
        assert result["status"] == "passed"
        with pytest.raises(FileExistsError):
            write_new_json(root / "run_manifest.json", {"status": "changed"})
        (root / "run_manifest.json").write_text('{"status":"tampered"}\n')
        with pytest.raises(RecostArtifactError, match="mismatch"):
            validate_checksums(root)


def test_nan_and_symlink_fail_closed():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with pytest.raises(RecostArtifactError):
            write_new_json(root / "bad.json", {"value": float("nan")})
        write_new_bytes(root / "real.txt", b"data")
        (root / "link.txt").symlink_to(root / "real.txt")
        with pytest.raises(RecostArtifactError, match="symlink"):
            write_checksums(root)

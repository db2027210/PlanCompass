#!/usr/bin/env python3
"""Release the formal claim pause only for the sealed fresh T-SGD manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time


MANIFEST_SHA256 = "40811927a11d52381343e7a23168b7d9c28c45a3628a7e5235ef975d3c0fb37f"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=Path, required=True)
    args = parser.parse_args()
    attempt = args.attempt.resolve()
    manifest = attempt / "fresh_tsgd_generation_manifest_v1.json"
    marker = attempt / "pause_before_next_formal_claim.json"
    archived = attempt / "released_pause_marker_manifest_40811927.json"
    audit = attempt / "initial_batch_release_manifest_40811927.json"
    if sha256(manifest) != MANIFEST_SHA256:
        raise RuntimeError("fresh T-SGD generation manifest SHA mismatch")
    if not marker.is_file() or archived.exists() or audit.exists():
        raise RuntimeError("pause marker release paths are not in the exact initial state")
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    if (
        marker_payload.get("pause") is not True
        or marker_payload.get("global_ordinal_after_completed_point") != 42
    ):
        raise RuntimeError("pause marker does not guard the expected p42 boundary")
    counters = {
        "replay_counter.txt": 42,
        "formal_search_replay_counter.txt": 0,
        "formal_search_accepted_counter.txt": 0,
        "formal_startup_baseline_counter.txt": 1,
    }
    for filename, expected in counters.items():
        actual = int((attempt / filename).read_text(encoding="utf-8").strip())
        if actual != expected:
            raise RuntimeError(f"counter mismatch before release: {filename}={actual}")
    if (attempt / "formal_points/point_0043").exists():
        raise RuntimeError("p43 already exists before initial-batch release")

    marker_sha = sha256(marker)
    payload = {
        "schema_version": 1,
        "status": "formal_initial_batch_released",
        "released_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "generation_manifest_path": str(manifest),
        "generation_manifest_sha256": MANIFEST_SHA256,
        "archived_pause_marker_path": str(archived),
        "archived_pause_marker_sha256": marker_sha,
        "counter_state_before_release": counters,
        "initial_batch_size": 5,
        "initial_batch_barrier_required": True,
        "single_candidate_early_accept_forbidden": True,
        "expand_only_if_all_first_five_unacceptable": True,
    }
    temporary_audit = audit.with_suffix(audit.suffix + ".tmp")
    with temporary_audit.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        marker.replace(archived)
        archived.chmod(0o444)
        temporary_audit.replace(audit)
        audit.chmod(0o444)
    except Exception:
        if archived.exists() and not marker.exists():
            archived.replace(marker)
        if temporary_audit.exists():
            temporary_audit.unlink()
        raise
    print(
        json.dumps(
            {
                "status": payload["status"],
                "audit_path": str(audit),
                "audit_sha256": sha256(audit),
                "archived_marker_sha256": marker_sha,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

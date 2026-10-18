#!/usr/bin/env python3
"""Create attempt10's missing accepted-shrink counter from audited evidence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List


ATTEMPT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt10_live_target_relative_20260723"
)
CONFIG = ATTEMPT / "config/live.json"
COUNTER = ATTEMPT / "formal_search_accepted_counter.txt"
AUDIT = ATTEMPT / "formal_search_accepted_counter_repair_audit.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    config: Dict[str, Any] = json.loads(CONFIG.read_text(encoding="utf-8"))
    protocol = config.get("formal_outer_protocol") or {}
    configured_counter = Path(
        str(protocol.get("formal_search_accepted_counter_path") or "")
    )
    if configured_counter != COUNTER:
        raise RuntimeError(
            f"configured counter path mismatch: {configured_counter} != {COUNTER}"
        )
    configured_start = int(protocol.get("formal_search_accepted_count_start", -1))
    if configured_start != 0:
        raise RuntimeError(f"attempt10 is not a fresh zero-count run: {configured_start}")
    resume_keys = sorted(
        key
        for key in protocol
        if "resume_checkpoint" in key or key.startswith("resume_")
    )
    if resume_keys:
        raise RuntimeError(f"attempt10 unexpectedly contains resume state: {resume_keys}")

    history_evidence: List[Dict[str, Any]] = []
    accepted_updates: List[Dict[str, Any]] = []
    for history_path in sorted((ATTEMPT / "history").glob("*.jsonl")):
        event_count = 0
        for line_number, raw in enumerate(
            history_path.read_text(encoding="utf-8", errors="replace").splitlines(),
            1,
        ):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event_count += 1
            if row.get("event") == "gradient_parameter_range_update":
                accepted_updates.append(
                    {
                        "path": str(history_path),
                        "line": line_number,
                        "round_index": row.get("round_index"),
                        "accepted_shrink_count": row.get("accepted_shrink_count"),
                    }
                )
        history_evidence.append(
            {
                "path": str(history_path),
                "size": history_path.stat().st_size,
                "sha256": sha256(history_path),
                "parsed_event_count": event_count,
            }
        )
    if accepted_updates:
        raise RuntimeError(
            "refusing zero initialization: attempt10 already has accepted range updates: "
            f"{accepted_updates}"
        )

    payload = {
        "schema_version": 1,
        "status": "repaired",
        "repair_epoch_seconds": time.time(),
        "attempt": str(ATTEMPT),
        "counter_path": str(COUNTER),
        "counter_value": configured_start,
        "basis": {
            "config_path": str(CONFIG),
            "config_sha256": sha256(CONFIG),
            "formal_search_accepted_count_start": configured_start,
            "resume_keys": resume_keys,
            "authoritative_accept_event": "gradient_parameter_range_update",
            "accepted_update_events_found": accepted_updates,
            "history_snapshot": history_evidence,
        },
        "statement": (
            "Value 0 is initialized from the fresh attempt10 contract and the "
            "absence of authoritative accepted range-update events; no acceptance "
            "was inferred from attempt9 or fabricated."
        ),
    }
    temporary_audit = AUDIT.with_suffix(".json.tmp")
    temporary_audit.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with temporary_audit.open("r+", encoding="utf-8") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    temporary_audit.replace(AUDIT)

    descriptor = os.open(COUNTER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, f"{configured_start}\n".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(
        f"repaired counter={configured_start} path={COUNTER} audit={AUDIT}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

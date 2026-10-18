#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mysql_plan_clustering import validate_canonical_plan_bundle  # noqa: E402


def verify_sidecar(path: Path) -> tuple[dict, str]:
    value = json.loads(path.read_text())
    canonical = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if path.read_bytes() != canonical:
        raise ValueError(f"noncanonical JSON: {path}")
    digest = hashlib.sha256(canonical).hexdigest()
    if path.with_name(path.name + ".sha256").read_text() != f"{digest}  {path.name}\n":
        raise ValueError(f"sidecar mismatch: {path}")
    return value, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.run_root.resolve()
    raw = sorted((root / "artifacts/raw_explain").glob("*.json"))
    if len(raw) != 9 or any(not isinstance(json.loads(path.read_text()), dict) for path in raw):
        raise ValueError("raw EXPLAIN coverage invalid")
    running = json.loads((root / "readback/mysql_inspect_running.json").read_text())[0]
    host = running["HostConfig"]
    if host["NanoCpus"] != 3_000_000_000 or host["CpusetCpus"] != "18-20,54-56" \
            or host["CpusetMems"] != "1" or host["Memory"] != 6_442_450_944 \
            or host["MemorySwap"] != 6_442_450_944 or host["PidsLimit"] != 512:
        raise ValueError("JOB envelope mismatch")
    if host["Binds"] != ["/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/.envelope_work/job_envelope_probe_20260722T223739Z/datadir:/var/lib/mysql:rw"]:
        raise ValueError("session-owned work copy mismatch")
    if (root / "readback/raw_explain_count.txt").read_text().strip() != "9":
        raise ValueError("raw EXPLAIN count readback mismatch")
    before = json.loads((root / "readback/pg_before.json").read_text())[0]
    after = json.loads((root / "readback/pg_after.json").read_text())[0]
    for key in ("Id", "RestartCount"):
        if before[key] != after[key]:
            raise ValueError("PG identity/restart changed")
    if before["State"]["Running"] is not True or after["State"]["Running"] is not True:
        raise ValueError("PG did not remain running")

    prior = None
    owner_rows = []
    for number, line in enumerate((root / "mutex_journal_after.jsonl").read_text().splitlines(), 1):
        row = json.loads(line)
        unsigned = {key: value for key, value in row.items() if key != "event_sha256"}
        actual = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if row["event_sha256"] != actual or row["previous_event_sha256"] != prior:
            raise ValueError(f"mutex journal chain failure line {number}")
        prior = row["event_sha256"]
        if row["owner_id"] == "job_strict_raw_explain_20260723":
            owner_rows.append(row)
    events = [row["event"] for row in owner_rows[-5:]]
    if events != ["lock_acquired", "preflight_zero_pressure_passed", "handoff_acquired", "recovery_checksum_scope_complete", "lock_released"] \
            or owner_rows[-1]["payload"]["release_reason"] != "complete":
        raise ValueError(f"successful mutex lifecycle absent: {events}")

    bundle, bundle_sha = verify_sidecar(root / "artifacts/canonical_plan_bundle.json")
    evidence, evidence_sha = verify_sidecar(root / "job_strict_plan_evidence.json")
    if validate_canonical_plan_bundle(bundle)["valid"] is not True \
            or bundle["strict_cluster_usable"] is not True \
            or bundle["strict_cluster_ineligible_reasons"] != [] \
            or bundle["plan_similarity_cluster_id"] != bundle["strict_canonical_physical_bundle_sha256"]:
        raise ValueError("JOB strict bundle invalid")
    if evidence["strict_cluster_usable"] is not True \
            or evidence["canonical_plan_bundle"]["sha256"] != bundle_sha:
        raise ValueError("JOB evidence mismatch")
    print(json.dumps({"bundle_sha256": bundle_sha, "evidence_sha256": evidence_sha,
                      "plan_similarity_cluster_id": bundle["plan_similarity_cluster_id"],
                      "raw_explains": 9, "status": "PASS"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error), "status": "FAIL"}, sort_keys=True))
        raise SystemExit(1)

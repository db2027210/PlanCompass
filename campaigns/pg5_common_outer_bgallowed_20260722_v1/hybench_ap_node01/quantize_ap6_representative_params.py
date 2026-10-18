#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List


ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
RANK1_EXPECTED_SHOW = {
    "seq_page_cost": "0.910528",
    "random_page_cost": "3.72627",
    "cpu_tuple_cost": "0.0115956",
    "cpu_operator_cost": "0.00289889",
    "parallel_setup_cost": "936.02",
    "parallel_tuple_cost": "0.0851735",
}


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def round_trip(
    continuous: Dict[str, float],
    *,
    image: str,
    pgdata: Path,
    rank: int,
) -> Dict[str, Any]:
    if set(continuous) != set(ACTIVE_PARAMS):
        raise ValueError(f"rank {rank}: continuous point must contain exactly AP6")
    script = r'''
set -eu
while [ "$#" -gt 0 ]; do
  setting_name=$1
  raw_value=$2
  shift 2
  first_show=$(postgres -D /var/lib/postgresql/data -C "$setting_name" -c "$setting_name=$raw_value")
  second_show=$(postgres -D /var/lib/postgresql/data -C "$setting_name" -c "$setting_name=$first_show")
  printf '%s\t%s\t%s\n' "$setting_name" "$first_show" "$second_show"
done
'''
    probe_name = f"p24_ap6_guc_probe_{os.getpid()}_{rank}"
    command = [
        "docker",
        "run",
        "--name",
        probe_name,
        "--rm",
        "--user",
        "postgres",
        "--volume",
        f"{pgdata}:/var/lib/postgresql/data:ro",
        "--entrypoint",
        "sh",
        image,
        "-c",
        script,
        "roundtrip",
    ]
    for name in ACTIVE_PARAMS:
        value = float(continuous[name])
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"rank {rank}: invalid continuous {name}={value}")
        command.extend([name, format(value, ".17g")])
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        subprocess.run(
            ["docker", "rm", "-f", probe_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        raise RuntimeError(
            f"rank {rank}: PostgreSQL GUC probe failed: {result.stderr.strip()}"
        )
    rows: Dict[str, Dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        name, first_show, second_show = line.split("\t")
        if name not in ACTIVE_PARAMS or first_show != second_show:
            raise RuntimeError(f"rank {rank}: non-idempotent GUC round-trip: {line}")
        applied = float(first_show)
        if not math.isfinite(applied) or applied <= 0.0:
            raise RuntimeError(f"rank {rank}: invalid applied GUC: {line}")
        rows[name] = {
            "continuous_value": float(continuous[name]),
            "first_show": first_show,
            "second_show": second_show,
            "applied_value": applied,
            "idempotent": True,
        }
    if set(rows) != set(ACTIVE_PARAMS):
        raise RuntimeError(f"rank {rank}: incomplete GUC probe output")
    applied_params = {name: rows[name]["applied_value"] for name in ACTIVE_PARAMS}
    return {
        "source_continuous_params": continuous,
        "continuous_signature_sha256": canonical_sha256(continuous),
        "applied_params": applied_params,
        "applied_signature_sha256": canonical_sha256(applied_params),
        "guc_roundtrip": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-plan", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--pgdata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection_plan = args.selection_plan.resolve()
    pgdata = args.pgdata.resolve()
    with selection_plan.open("r", encoding="utf-8") as handle:
        plan = json.load(handle)
    representatives = plan.get("representative_order")
    if not isinstance(representatives, list) or len(representatives) != 8:
        raise RuntimeError("selection plan must contain exactly 8 representatives")

    records: List[Dict[str, Any]] = []
    for representative in representatives:
        rank = int(representative["rank"])
        continuous = {
            name: float(representative["optimized_params"][name])
            for name in ACTIVE_PARAMS
        }
        record = round_trip(
            continuous,
            image=args.image,
            pgdata=pgdata,
            rank=rank,
        )
        record.update(
            {
                "rank": rank,
                "replay_order": representative["replay_order"],
                "subset_id": representative["subset_id"],
                "active_subset": representative["active_subset"],
                "exact_plan_cluster_id": representative["exact_plan_cluster_id"],
                "optimized_params": record["applied_params"],
            }
        )
        records.append(record)

    rank1 = next(record for record in records if record["rank"] == 1)
    actual_rank1_show = {
        name: rank1["guc_roundtrip"][name]["first_show"]
        for name in ACTIVE_PARAMS
    }
    if actual_rank1_show != RANK1_EXPECTED_SHOW:
        raise RuntimeError(
            f"targeted rank1 round-trip regression failed: {actual_rank1_show}"
        )
    if len({record["applied_signature_sha256"] for record in records}) != 8:
        raise RuntimeError("applied representative points must remain unique")

    payload = {
        "schema_version": 1,
        "status": "postgresql_guc_roundtrip_complete",
        "formal_active_parameters": list(ACTIVE_PARAMS),
        "fixed_excluded_parameter": {"cpu_index_tuple_cost": 0.005},
        "selection_plan_path": str(selection_plan),
        "selection_plan_sha256": file_sha256(selection_plan),
        "postgresql_image": args.image,
        "probe_pgdata": str(pgdata),
        "probe_semantics": (
            "postgres_-C_after_parse_then_second_-C_idempotence_before_real_replay"
        ),
        "continuous_values_role": "audit_only_never_applied_or_used_as_shrink_anchor",
        "applied_values_role": (
            "signature_plan_fingerprint_real_replay_acceptance_and_shrink_anchor"
        ),
        "targeted_rank1_regression": {
            "expected_show": RANK1_EXPECTED_SHOW,
            "actual_show": actual_rank1_show,
            "passed": True,
        },
        "representatives": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": file_sha256(args.output),
                "representative_count": len(records),
                "rank1_applied_params": rank1["applied_params"],
                "rank1_applied_signature_sha256": rank1[
                    "applied_signature_sha256"
                ],
                "targeted_rank1_regression_passed": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

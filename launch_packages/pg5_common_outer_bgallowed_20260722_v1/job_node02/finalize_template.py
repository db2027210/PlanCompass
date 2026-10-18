#!/usr/bin/env python3
"""Publish the lane-local JOB template manifest after a clean shutdown."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
TABLES = (
    "aka_name", "aka_title", "cast_info", "char_name", "comp_cast_type",
    "company_name", "company_type", "complete_cast", "info_type", "keyword",
    "kind_type", "link_type", "movie_companies", "movie_info", "movie_info_idx",
    "movie_keyword", "movie_link", "name", "person_info", "role_type", "title",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    template_root = LANE_ROOT / "template" / "job"
    pgdata = template_root / "pgdata"
    build = LANE_ROOT / "template_build"
    row_counts = {}
    for line in (build / "row_counts.tsv").read_text(encoding="utf-8").splitlines():
        table, count = line.split("\t")
        row_counts[f"public.{table}"] = int(count)
    if tuple(row.split(".", 1)[1] for row in row_counts) != TABLES:
        raise RuntimeError("JOB row-count evidence is incomplete or out of order")
    sizes = [int(line) for line in (build / "regular_file_sizes.txt").read_text(encoding="utf-8").splitlines() if line]
    control = pgdata / "global" / "pg_control"
    manifest = {
        "schema_version": 1,
        "template_id": "pg5_common_outer_bgallowed_20260722_v1_job_node02_pg17",
        "freeze_id": "pg5_common_outer_bgallowed_20260722_v1",
        "global_campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "engine": "postgresql",
        "kind": "job",
        "database": "imdb_job",
        "status": "frozen",
        "provision_mode": "fresh lane-local initdb plus exact 21-table JOB CSV load",
        "built_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "postgresql_version": (build / "postgresql_version.txt").read_text(encoding="utf-8").strip(),
        "default_statistics_target": int((build / "default_statistics_target.txt").read_text(encoding="utf-8").strip()),
        "statistics_policy": "ANALYZE and CHECKPOINT once before clean shutdown; no ANALYZE between points",
        "row_counts": row_counts,
        "database_size_bytes": int((build / "database_size_bytes.txt").read_text(encoding="utf-8").strip()),
        "template_relative_path": "job",
        "template_pgdata": str(pgdata),
        "source_sql": {
            "schema.sql": sha256_file(LANE_ROOT / "source" / "job_sql" / "schema.sql"),
            "fkindexes.sql": sha256_file(LANE_ROOT / "source" / "job_sql" / "fkindexes.sql"),
        },
        "source_csv_checksums_sha256": sha256_file(LANE_ROOT / "source" / "jobdata" / "checksums.sha256"),
        "build_runtime": {
            "copy_mode": "fresh_lane_local_load",
            "source_pgdata": None,
            "source_pg_control_sha256": None,
            "pg_control_sha256_after_clean_shutdown": sha256_file(control),
            "pgdata_bytes": sum(sizes),
            "regular_file_count": len(sizes),
        },
    }
    path = template_root / "template_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "frozen", "manifest": str(path), "pg_control_sha256": manifest["build_runtime"]["pg_control_sha256_after_clean_shutdown"]}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

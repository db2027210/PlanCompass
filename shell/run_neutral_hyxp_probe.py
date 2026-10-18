#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


SOURCE_HYBENCH_DIR = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/hybench_runtime/HyBench-2024-master"
)
SOURCE_CONFIG = ROOT_DIR / "config/binary_plan_tuning_hybenchxp.json"
SETUP_SQL = ROOT_DIR / "shell/hybenchxp_none_physio_setup.sql"
PROFILE_SQL = {
    "950_475": ROOT_DIR / "shell/hybenchxp_none_ap6_physio.sql",
    "600_300": ROOT_DIR / "shell/hybenchxp_none_ap6_physio_600_300.sql",
}
MEMORY_WORKSET_SQL = {
    "checking": ROOT_DIR / "shell/hybenchxp_none_memory_workset_checking.sql",
}


def prepare_neutral_hybench_dir(label: str) -> Path:
    hybench_dir = Path("/tmp") / f"hw_{label}"
    hybench_dir.mkdir(parents=True, exist_ok=True)
    for name in ("lib", "conf", "Data_10x", "Data_100x"):
        link = hybench_dir / name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(SOURCE_HYBENCH_DIR / name)
    launcher = hybench_dir / "run"
    shutil.copy2(SOURCE_HYBENCH_DIR / "hybench", launcher)
    launcher.chmod(0o755)
    return hybench_dir


def write_neutral_config(
    label: str,
    hybench_dir: Path,
    *,
    interval_s: float,
    warmup_s: float,
    min_stable_samples: int,
) -> Path:
    cfg = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    validation_cfg = cfg["binary_plan_tuning"]["validation"]
    docker_cfg = validation_cfg.setdefault("docker_runtime", {})
    docker_cfg["interval_sec"] = interval_s
    docker_cfg["warmup_sec"] = warmup_s
    docker_cfg["min_stable_samples"] = min_stable_samples
    docker_cfg["io_mode"] = "cgroup"
    docker_cfg["checkpoint_before_validation"] = True
    docker_cfg["checkpoint_after_validation"] = True
    hybench_cfg = validation_cfg["hybench_xp"]
    hybench_cfg["hybench_dir"] = str(hybench_dir)
    hybench_cfg["launcher"] = str(hybench_dir / "run")
    hybench_cfg["base_props_file"] = str(hybench_dir / "conf/pg.props")
    hybench_cfg["statement_file"] = str(hybench_dir / "conf/stmt_postgres.toml")
    path = Path("/tmp") / f"cfg_{label}.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def copy_sql(label: str, src: Path, prefix: str) -> Path:
    dst = Path("/tmp") / f"{prefix}_{label}.sql"
    shutil.copy2(src, dst)
    return dst


def generated_ap6_write_sql(
    *,
    insert_rows: int,
    update_rows: int,
    payload_repeat: int,
    update_shards: int,
) -> str:
    if insert_rows <= 0:
        raise SystemExit("--insert-rows must be positive when --profile is not set")
    if update_rows < 0:
        raise SystemExit("--update-rows must be non-negative")
    if payload_repeat <= 0:
        raise SystemExit("--payload-repeat must be positive")
    if update_shards <= 0:
        raise SystemExit("--update-shards must be positive")
    return f"""INSERT INTO bonect_hybench_physio_probe(bucket, payload)
SELECT (random() * 1000)::int, repeat(md5(random()::text), {payload_repeat})
FROM generate_series(1, {insert_rows});

UPDATE bonect_hybench_physio_probe
SET payload = repeat(md5(random()::text), {payload_repeat}),
    updated_at = clock_timestamp()
WHERE id IN (
    SELECT id
    FROM bonect_hybench_physio_probe
    WHERE (id % {update_shards}) = (pg_backend_pid() % {update_shards})
    ORDER BY random()
    LIMIT {update_rows}
);

SELECT count(*)
FROM bonect_hybench_physio_probe
WHERE bucket BETWEEN 100 AND 900;
"""


def ap_sql_for_args(label: str, args: argparse.Namespace) -> Path:
    if args.profile:
        sql = PROFILE_SQL[args.profile].read_text(encoding="utf-8")
    else:
        sql = generated_ap6_write_sql(
            insert_rows=args.insert_rows,
            update_rows=args.update_rows,
            payload_repeat=args.payload_repeat,
            update_shards=args.update_shards,
        )
    for extra_sql in args.append_sql:
        sql += "\n" + extra_sql.strip() + "\n"
    for key in args.memory_workset:
        sql += "\n" + MEMORY_WORKSET_SQL[key].read_text(encoding="utf-8").strip() + "\n"
    for extra_path in args.append_sql_file:
        sql += "\n" + Path(extra_path).expanduser().read_text(encoding="utf-8").strip() + "\n"
    dst = Path("/tmp") / f"ap_{label}.sql"
    dst.write_text(sql, encoding="utf-8")
    return dst


def main() -> int:
    if os.environ.get("BONECT_ENABLE_NEUTRAL_HYXP_PROBE") != "1":
        print(
            "run_neutral_hyxp_probe disabled; set "
            "BONECT_ENABLE_NEUTRAL_HYXP_PROBE=1 to enable.",
            flush=True,
        )
        return 0

    parser = argparse.ArgumentParser(description="Run HyBench XP probe with neutral command-line paths.")
    parser.add_argument("--profile", choices=sorted(PROFILE_SQL), default="")
    parser.add_argument("--insert-rows", type=int, default=0)
    parser.add_argument("--update-rows", type=int, default=0)
    parser.add_argument("--payload-repeat", type=int, default=32)
    parser.add_argument("--update-shards", type=int, default=3)
    parser.add_argument("--append-sql", action="append", default=[])
    parser.add_argument("--append-sql-file", action="append", default=[])
    parser.add_argument("--memory-workset", action="append", choices=sorted(MEMORY_WORKSET_SQL), default=[])
    parser.add_argument("--worker", default="cpu_mem_w2")
    parser.add_argument("--xapclient", type=int, default=1)
    parser.add_argument("--xtpclient", type=int, default=0)
    parser.add_argument("--xp-run-mins", type=int, default=2)
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--warmup-s", type=float, default=5.0)
    parser.add_argument("--min-stable-samples", type=int, default=20)
    parser.add_argument("--no-restore", action="store_true")
    parser.add_argument("--label", default="")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%dT%H%M%S")
    profile_label = args.profile or f"{args.insert_rows}_{args.update_rows}"
    label = args.label or f"n_{stamp}_{args.worker}_x{args.xapclient}_{profile_label}"
    output_dir = Path(args.output_dir or (Path("/tmp") / f"out_{label}"))
    output_dir.mkdir(parents=True, exist_ok=True)
    hybench_dir = prepare_neutral_hybench_dir(label)
    cfg_path = write_neutral_config(
        label,
        hybench_dir,
        interval_s=args.interval_s,
        warmup_s=args.warmup_s,
        min_stable_samples=args.min_stable_samples,
    )
    setup_sql = copy_sql(label, SETUP_SQL, "setup")
    ap_sql = ap_sql_for_args(label, args)

    from shell import probe_hybenchxp_baseline

    sys.argv = [
        "neutral_probe",
        "--config",
        str(cfg_path),
        "--output-dir",
        str(output_dir),
        "--label",
        label,
        "--worker",
        args.worker,
        "--target",
        "none",
    ]
    if args.no_restore:
        sys.argv.append("--no-restore")
    sys.argv.extend([
        "--xtpclient",
        str(args.xtpclient),
        "--xapclient",
        str(args.xapclient),
        "--xp-run-mins",
        str(args.xp_run_mins),
        "--io-mode",
        "cgroup",
        "--ap-query-sequence",
        "6",
        "--statement-sql-file",
        f"AP-6={ap_sql}",
        "--setup-sql-file",
        str(setup_sql),
        "--check-none-gate",
        "--ignore-sigterm",
    ])
    return probe_hybenchxp_baseline.main()


if __name__ == "__main__":
    raise SystemExit(main())

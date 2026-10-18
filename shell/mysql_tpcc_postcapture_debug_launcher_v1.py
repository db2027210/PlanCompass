#!/usr/bin/env python3
"""Launch the TPC-C one-round debug chain only from a sealed capture registry."""

from __future__ import annotations

import argparse
import fcntl
import importlib
import importlib.util
import json
from pathlib import Path
import re
import socket


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load sealed module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _outer_debug(
    legacy, bundle, manifest, capture, config, authority_path
) -> None:
    legacy.verify_sidecar(authority_path)
    authority = legacy.load(authority_path)
    checkpoint = Path(
        config["formal_outer_protocol"]["formal_real_r0_checkpoint_path"]
    )
    if (
        authority.get("schema_version")
        != "mysql_tpcc_local_outer_debug_authority_v1"
        or authority.get("status") != "OUTER_DEBUG_AUTHORIZED"
        or authority.get("bundle_id") != bundle.name
        or authority.get("capture_authority_sha256") != capture["sha256"]
        or authority.get("r0_checkpoint_sha256") != legacy.sha256(checkpoint)
        or authority.get("all_nonempty_subset_count") != 15
        or authority.get("fixed_global_top_k") != 5
        or authority.get("accepted_round_authorized") is not False
    ):
        raise RuntimeError("outer debug authority differs")
    legacy.load_source(manifest)
    from formal_mysql_sgd_checkpoint import FormalProductionR0Ledger
    from formal_mysql_sgd_campaign import _bind_runtime_estimator_baseline
    from formal_mysql_sgd_orchestration import (
        FormalPointJournal, build_replay_driver,
    )
    from mysql_parameter_optimizer import run_optimization
    services = importlib.import_module(
        "mysql_algorithm_c_local_services"
    ).build(config)
    result_path = None
    try:
        baseline = FormalProductionR0Ledger.load_verified(
            checkpoint,
            campaign_identity=legacy.campaign_identity(
                bundle, config, capture
            ),
        )
        journal = FormalPointJournal(
            Path(
                config["formal_outer_protocol"][
                    "formal_point_journal_root"
                ]
            ),
            (
                "/home/anonymous/miniconda3/envs/py313/bin/python",
                str((bundle / "launcher.py").resolve()),
                "--mode", "outer-debug",
                "--bundle", str(bundle),
                "--run-token", str(
                    Path(config["output"]["directory"]).name
                ),
                "--outer-authority", str(authority_path.resolve()),
            ),
        )
        driver = build_replay_driver(
            "tpcc", services["registration"],
            services["dependencies"], journal,
        )
        result = run_optimization(
            _bind_runtime_estimator_baseline(config, baseline),
            client=services["sql_client"],
            real_replay_callback=driver,
            baseline_validation=baseline,
            method_id="experience_table_sgd",
        )
        result_path = Path(str(result.result_path))
    finally:
        cleanup = services["campaign_cleanup"]()
        if cleanup.get("zero_pressure_verified") is not True:
            raise RuntimeError("outer cleanup did not prove zero pressure")
    if result_path is None:
        raise RuntimeError("outer debug result authority is absent")
    legacy.atomic(
        Path(config["output"]["directory"])
        / "outer_debug_completion.json",
        {
            "schema_version":
                "mysql_tpcc_local_outer_debug_completion_v2",
            "status": "DEBUG_ROUND_COMPLETE",
            "result_path": str(result_path),
            "result_sha256": legacy.sha256(result_path),
            "point_journal_root": config["formal_outer_protocol"][
                "formal_point_journal_root"
            ],
            "point_journals_required": True,
            "accepted_round_authorized": False,
            "zero_pressure_verified": True,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("preflight", "r0-only", "seal-outer", "outer-debug"),
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run-token", required=True)
    parser.add_argument("--outer-authority", type=Path)
    args = parser.parse_args()
    if socket.gethostname() != "compute00":
        raise RuntimeError("TPC-C post-capture debug is bound to compute00")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{16,128}", args.run_token):
        raise RuntimeError("invalid fresh run token")
    bundle = args.bundle.resolve(strict=True)
    promotion = _load_module(
        "sealed_tpcc_postcapture_promotion",
        bundle / "postcapture_promotion.py",
    )
    manifest = promotion.verify_debug_bundle(bundle)
    capture = promotion.verify_sealed_registry(bundle / "capture_registry")
    legacy = _load_module(
        "sealed_tpcc_debug_runtime", bundle / "base_debug_runtime.py"
    )
    config = legacy.runtime_config(
        bundle, manifest, capture, args.run_token
    )
    if args.mode == "preflight":
        print(json.dumps({
            "status": "PASS_POSTCAPTURE_DEBUG_R0_READY",
            "bundle_id": bundle.name,
            "capture_registry_id": manifest["capture_registry_id"],
            "accepted_round_authorized": False,
        }, sort_keys=True))
        return 0
    lock = legacy.acquire_lane_lock()
    try:
        if args.mode == "r0-only":
            if args.outer_authority is not None:
                raise RuntimeError("r0-only rejects outer authority")
            legacy.r0_only(bundle, manifest, capture, config)
        elif args.mode == "seal-outer":
            if args.outer_authority is None:
                raise RuntimeError("seal-outer requires output authority path")
            legacy.seal_outer(
                bundle, manifest, capture, config, args.outer_authority
            )
        else:
            if args.outer_authority is None:
                raise RuntimeError("outer-debug requires sealed authority")
            _outer_debug(
                legacy, bundle, manifest, capture, config,
                args.outer_authority,
            )
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail-closed launcher for a promoted TPC-C ten-accepted-round bundle."""

from __future__ import annotations

import argparse
import fcntl
import importlib
import importlib.util
import json
from pathlib import Path
import re
import socket


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load sealed module: {path}")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def _verify_authority(
    legacy, bundle: Path, capture, config, authority_path: Path
) -> None:
    legacy.verify_sidecar(authority_path)
    authority = legacy.load(authority_path)
    checkpoint = Path(
        config["formal_outer_protocol"]["formal_real_r0_checkpoint_path"]
    )
    if (
        authority.get("schema_version")
        != "mysql_tpcc_formal10_outer_authority_v1"
        or authority.get("status") != "FORMAL10_AUTHORIZED"
        or authority.get("bundle_id") != bundle.name
        or authority.get("capture_authority_sha256") != capture["sha256"]
        or authority.get("r0_checkpoint_sha256") != legacy.sha256(checkpoint)
        or authority.get("target_accepted_rounds") != 10
        or authority.get("initial_accepted_rounds") != 0
        or authority.get("all_nonempty_subset_count") != 15
        or authority.get("fixed_global_top_k") != 5
    ):
        raise RuntimeError("formal10 authority differs")


def _seal(
    legacy, bundle: Path, manifest, capture, config, output: Path
) -> None:
    legacy.load_source(manifest)
    from formal_mysql_sgd_checkpoint import FormalProductionR0Ledger
    checkpoint = Path(
        config["formal_outer_protocol"]["formal_real_r0_checkpoint_path"]
    )
    FormalProductionR0Ledger.load_verified(
        checkpoint,
        campaign_identity=legacy.campaign_identity(
            bundle, config, capture
        ),
    )
    legacy.atomic(output, {
        "schema_version": "mysql_tpcc_formal10_outer_authority_v1",
        "status": "FORMAL10_AUTHORIZED",
        "bundle_id": bundle.name,
        "capture_authority_sha256": capture["sha256"],
        "r0_checkpoint_sha256": legacy.sha256(checkpoint),
        "original_gradient_only": True,
        "parameter_count": 4,
        "all_nonempty_subset_count": 15,
        "fixed_global_top_k": 5,
        "target_accepted_rounds": 10,
        "initial_accepted_rounds": 0,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "fresh_runtime_gate_each_round": True,
        "strict_accepted_counter": True,
    })


def _run(legacy, promotion, bundle, manifest, capture, config, authority):
    _verify_authority(
        legacy, bundle, capture, config, authority
    )
    legacy.load_source(manifest)
    from formal_mysql_sgd_checkpoint import FormalProductionR0Ledger
    from formal_mysql_sgd_campaign import _bind_runtime_estimator_baseline
    from formal_mysql_sgd_orchestration import (
        FormalPointJournal, build_replay_driver,
    )
    from mysql_parameter_optimizer import run_optimization
    checkpoint = Path(
        config["formal_outer_protocol"]["formal_real_r0_checkpoint_path"]
    )
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
                "--mode", "formal10",
                "--bundle", str(bundle),
                "--run-token", str(
                    Path(config["output"]["directory"]).name
                ),
                "--formal-authority", str(authority.resolve()),
            ),
        )
        driver = build_replay_driver(
            "tpcc", services["registration"], services["dependencies"], journal
        )
        result = run_optimization(
            _bind_runtime_estimator_baseline(config, baseline),
            client=services["sql_client"],
            real_replay_callback=driver,
            baseline_validation=baseline,
            method_id="experience_table_sgd",
        )
        if (
            getattr(result, "campaign_complete", False) is not True
            or getattr(result, "accepted_shrink_count", None) != 10
        ):
            raise RuntimeError(
                "formal10 campaign ended before ten accepted rounds"
            )
        result_path = Path(str(result.result_path)).resolve(strict=True)
        promotion.verify_formal_campaign_result(
            result_path,
            Path(
                config["formal_outer_protocol"][
                    "formal_point_journal_root"
                ]
            ),
        )
    finally:
        cleanup = services["campaign_cleanup"]()
        if cleanup.get("zero_pressure_verified") is not True:
            raise RuntimeError("formal10 cleanup did not prove zero pressure")
    if result_path is None:
        raise RuntimeError("formal10 result authority is absent")
    output = Path(config["output"]["directory"])
    legacy.atomic(output / "formal10_completion.json", {
        "schema_version": "mysql_tpcc_formal10_completion_v1",
        "status": "COMPLETE_10_ACCEPTED_ROUNDS",
        "bundle_id": bundle.name,
        "capture_registry_id": manifest["capture_registry_id"],
        "result_path": str(result_path),
        "result_sha256": legacy.sha256(result_path),
        "accepted_rounds": 10,
        "zero_pressure_verified": True,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("preflight", "r0-only", "seal-formal10", "formal10"),
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run-token", required=True)
    parser.add_argument("--formal-authority", type=Path)
    args = parser.parse_args()
    if socket.gethostname() != "compute00":
        raise RuntimeError("TPC-C formal10 is bound to compute00")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{16,128}", args.run_token):
        raise RuntimeError("invalid fresh run token")
    bundle = args.bundle.resolve(strict=True)
    promotion = _module(
        "sealed_tpcc_postcapture_promotion",
        bundle / "postcapture_promotion.py",
    )
    manifest = promotion.verify_formal10_bundle(bundle)
    capture = promotion.verify_sealed_registry(
        bundle / "capture_registry"
    )
    legacy = _module(
        "sealed_tpcc_debug_runtime", bundle / "base_debug_runtime.py"
    )
    config = legacy.runtime_config(
        bundle, manifest, capture, args.run_token
    )
    if args.mode == "preflight":
        print(json.dumps({
            "status": "PASS_FORMAL10_READY_RUNTIME_GATE_PENDING",
            "bundle_id": bundle.name,
            "target_accepted_rounds": 10,
            "initial_accepted_rounds": 0,
        }, sort_keys=True))
        return 0
    lock = legacy.acquire_lane_lock()
    try:
        if args.mode == "r0-only":
            if args.formal_authority is not None:
                raise RuntimeError("r0-only rejects formal authority")
            legacy.r0_only(bundle, manifest, capture, config)
        elif args.mode == "seal-formal10":
            if args.formal_authority is None:
                raise RuntimeError(
                    "seal-formal10 requires an output authority path"
                )
            _seal(
                legacy, bundle, manifest, capture, config,
                args.formal_authority,
            )
        else:
            if args.formal_authority is None:
                raise RuntimeError(
                    "formal10 requires a sealed formal authority"
                )
            _run(
                legacy, promotion, bundle, manifest, capture, config,
                args.formal_authority,
            )
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""XP v35: reuse sealed restore and bind R0 to authoritative estimators."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

try:
    import resume_xp_fresh0_fixed_contract_target10_v33 as v33
    import resume_xp_fresh0_fixed_contract_target10_v34 as v34
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v33 as v33
    from tools import resume_xp_fresh0_fixed_contract_target10_v34 as v34


SOURCE_RESTORE_AUDIT = Path(
    "/home/anonymous/program/resource_adaptive/history/"
    "xp_final_algorithm_fresh10_v34_20260803T1205/"
    "v34_pre_r0_restore_audit.json"
)
SOURCE_RESTORE_AUDIT_SHA256 = (
    "59d418c97ad20a74018d7baaa9551651fbbfcb041f719b515254e117e97bfe8a"
)
PLAN_ESTIMATOR_SHA256 = (
    "33fd489055dce710495f03236f4d38ec1010523f9c81a88b40a978bb3622a150"
)
RATIO_ESTIMATOR_SHA256 = (
    "d9485bfe5b1f394b0da01963f147df54f2f798e6e04d650f6cadd385dc578ca7"
)
_BASE_BOOTSTRAP = v34._BASE_BOOTSTRAP
_ORIGINAL_SUBPROCESS_RUN = subprocess.run


def verify_postgres_with_retry(
    config: Mapping[str, Any],
    *,
    attempts: int = 8,
    delay_s: float = 2.0,
    verify_func: Optional[Callable[[Mapping[str, Any]], Dict[str, Any]]] = None,
    sleep_func: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Run the sealed read-only PostgreSQL check with bounded readiness retry."""
    if attempts < 1:
        raise ValueError("XP v35 PostgreSQL retry attempts must be positive")
    if delay_s < 0:
        raise ValueError("XP v35 PostgreSQL retry delay must be nonnegative")
    pg = config.get("postgres") or {}
    endpoint = {
        "database": str(pg.get("dbname") or ""),
        "host": str(pg.get("host") or "127.0.0.1"),
        "port": int(pg.get("port") or 59630),
        "user": str(pg.get("user") or "postgres"),
    }
    verify = verify_func or v34._verify_postgres
    failures = []
    started = time.perf_counter()
    for attempt in range(1, attempts + 1):
        attempt_started = time.perf_counter()
        try:
            postgres = verify(config)
            return {
                "postgres": postgres,
                "attempt_count": attempt,
                "failure_count": len(failures),
                "failures": failures,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "endpoint": endpoint,
            }
        except Exception as exc:
            failures.append(
                {
                    "attempt": attempt,
                    "duration_seconds": round(
                        time.perf_counter() - attempt_started,
                        6,
                    ),
                    "exception_type": type(exc).__name__,
                    "exception_repr": repr(exc),
                    "exception_message": str(exc),
                }
            )
            if attempt < attempts:
                sleep_func(delay_s)
    diagnostics = {
        "attempt_count": attempts,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "endpoint": endpoint,
        "failures": failures,
    }
    raise RuntimeError(
        "XP v35 PostgreSQL readiness exhausted without mutation: "
        + json.dumps(diagnostics, sort_keys=True)
    )


def _authoritative_formal_root(bundle: Path) -> Path:
    root = bundle.resolve() / "authoritative_round9"
    expected = {
        root / "plan_resource_estimator.py": PLAN_ESTIMATOR_SHA256,
        root / "ratio_scale_resource_estimator.py": RATIO_ESTIMATOR_SHA256,
    }
    for path, digest in expected.items():
        if not path.is_file() or v33.sha256(path) != digest:
            raise RuntimeError(f"XP v35 estimator SHA mismatch: {path}")
    environment = copy.deepcopy(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root), str(bundle.resolve()), environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    code = (
        "import sys; "
        f"sys.path.insert(0, {json.dumps(str(root))}); "
        "from pathlib import Path; "
        "import plan_resource_estimator as p; "
        "import ratio_scale_resource_estimator as r; "
        "print(Path(p.__file__).resolve()); "
        "print(Path(r.__file__).resolve())"
    )
    completed = _ORIGINAL_SUBPROCESS_RUN(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
        cwd=str(root),
    )
    observed = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or observed != [str(path) for path in expected]:
        raise RuntimeError(
            "XP v35 authoritative estimator import failed: "
            f"returncode={completed.returncode} stdout={observed} "
            f"stderr={completed.stderr[-1000:]}"
        )
    return root


def reuse_restore_audit_before_r0(
    *,
    campaign: Path,
    state_store: Any,
    config: Path,
    restore_script: Path,
) -> Dict[str, Any]:
    """Reverify the v34 restore without executing the restore script again."""
    v34._assert_affinity()
    if (
        not SOURCE_RESTORE_AUDIT.is_file()
        or v33.sha256(SOURCE_RESTORE_AUDIT) != SOURCE_RESTORE_AUDIT_SHA256
    ):
        raise RuntimeError("XP v35 source restore audit SHA mismatch")
    source = v33.load(SOURCE_RESTORE_AUDIT)
    authority = v34._sealed_hashes(v34._authority_paths())
    if (
        source.get("status") != "complete"
        or source.get("physical_workload_calls_added") != 0
        or source.get("cpuset") != v34.EXPECTED_CPUSET_TEXT
        or source.get("restore_script_sha256") != v33.sha256(restore_script)
        or source.get("authority_sha256_before") != authority
        or source.get("authority_sha256_after") != authority
        or (source.get("postgres") or {}).get("database") != "hybench_sf10x"
        or (source.get("postgres") or {}).get("seven_parameter_baseline")
        != v34.EXPECTED_BASELINE
    ):
        raise RuntimeError("XP v35 source restore audit contract mismatch")
    state_before = copy.deepcopy(state_store.load())
    state_digest_before = v34._canonical_sha256(state_before)
    readiness = verify_postgres_with_retry(v33.load(config))
    postgres = readiness["postgres"]
    v34._assert_affinity()
    authority_after = v34._sealed_hashes(v34._authority_paths())
    state_after = state_store.load()
    state_digest_after = v34._canonical_sha256(state_after)
    if authority_after != authority or state_after != state_before:
        raise RuntimeError("XP v35 restore reuse verification mutated authority/state")
    audit = {
        "schema_version": "xp_v35_pre_r0_restore_reuse_audit_v1",
        "status": "complete",
        "physical_workload_calls_added": 0,
        "restore_reexecuted": False,
        "source_restore_audit": str(SOURCE_RESTORE_AUDIT),
        "source_restore_audit_sha256": SOURCE_RESTORE_AUDIT_SHA256,
        "restore_script_sha256": v33.sha256(restore_script),
        "cpuset": v34.EXPECTED_CPUSET_TEXT,
        "postgres": postgres,
        "postgres_readiness": readiness,
        "authority_sha256_verified": authority_after,
        "campaign_state_sha256_before": state_digest_before,
        "campaign_state_sha256_after": state_digest_after,
        "next_action": "authoritative_round9_explain_builder_then_fresh_r0",
        "completed_at_epoch": time.time(),
    }
    v34._atomic_json(campaign / "v35_pre_r0_restore_reuse_audit.json", audit)
    return audit


def _rewrite_builder_formal_root(
    command: Sequence[Any],
    *,
    bundle: Path,
    formal_root: Path,
) -> list[Any]:
    rewritten = list(command)
    if not rewritten or Path(str(rewritten[1])).name != (
        "build_xp_incumbent_revalidation_candidate.py"
    ):
        return rewritten
    if "--formal-root" not in rewritten:
        raise RuntimeError("XP v35 R0 builder lacks --formal-root")
    index = rewritten.index("--formal-root") + 1
    if Path(str(rewritten[index])).resolve() != bundle.resolve():
        raise RuntimeError("XP v35 R0 builder formal-root source mismatch")
    rewritten[index] = str(formal_root)
    return rewritten


def run_fresh_runtime_baseline_bootstrap_v35(**kwargs: Any) -> Dict[str, Any]:
    state_store = kwargs["state_store"]
    state = state_store.load()
    existing = state.get("fresh_runtime_baseline_revalidation") or {}
    if existing.get("status") == "complete":
        return _BASE_BOOTSTRAP(**kwargs)
    bundle = Path(kwargs["bundle"]).resolve()
    campaign = Path(kwargs["campaign"]).resolve()
    reuse_restore_audit_before_r0(
        campaign=campaign,
        state_store=state_store,
        config=Path(kwargs["config"]).resolve(),
        restore_script=Path(kwargs["restore_script"]).resolve(),
    )
    formal_root = _authoritative_formal_root(bundle)
    rewrite_audit = {
        "schema_version": "xp_v35_r0_builder_formal_root_audit_v1",
        "status": "sealed",
        "physical_workload_calls_added": 0,
        "formal_root": str(formal_root),
        "plan_resource_estimator_sha256": PLAN_ESTIMATOR_SHA256,
        "ratio_scale_resource_estimator_sha256": RATIO_ESTIMATOR_SHA256,
        "imports_verified": True,
    }
    v34._atomic_json(campaign / "v35_r0_builder_formal_root_audit.json", rewrite_audit)

    def run_with_formal_root(command: Sequence[Any], *args: Any, **options: Any) -> Any:
        rewritten = _rewrite_builder_formal_root(
            command,
            bundle=bundle,
            formal_root=formal_root,
        )
        return _ORIGINAL_SUBPROCESS_RUN(rewritten, *args, **options)

    observed_run = v33.subprocess.run
    v33.subprocess.run = run_with_formal_root
    try:
        return _BASE_BOOTSTRAP(**kwargs)
    finally:
        v33.subprocess.run = observed_run


def main() -> int:
    v33.run_fresh_runtime_baseline_bootstrap = (
        run_fresh_runtime_baseline_bootstrap_v35
    )
    return v33.main()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""XP v41: type-safe v39 exclusion handling and sealed pool reuse."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Optional


CONTRACT = "xp_v41_excluded_ids_type_safe_sealed_cont006_pool_resume_v1"
EXPECTED_V39_SHA256 = (
    "d5b793ddbaa8eea6fb17519570f83e2e3bac5bac7a20d9a71462e5b35bbbdf83"
)
EXPECTED_INSTALL_BUNDLE_SOURCE_SHA256 = (
    "13e4cfcd6dbb9ef35aba02af29957a6842956ac9ceb667a7f222459a2de0718b"
)
BROKEN_LOOP = "        for group in (active, corners, excluded):"
FIXED_LOOP = "        for group in (active, corners):"
RETURN_BLOCK = "        return active, corners, excluded"
TYPE_SAFE_RETURN_BLOCK = '''        if not isinstance(excluded, list) or any(
            not isinstance(identifier, str) or not identifier
            for identifier in excluded
        ):
            raise RuntimeError("XP v41 excluded fingerprints must be non-empty string IDs")
        return active, corners, excluded'''

PROJECT = Path("/home/anonymous/program/resource_adaptive")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
POOL = CAMPAIGN / "batches/cont006/explain_only/explain_only_generation.json"
EXPECTED_POOL_SHA256 = (
    "aa326f82f2f5727fa5288a1484be6eaa3b9fd2be2b373b94f710e5a4ed747751"
)
EXPECTED_FORMAL_SOURCES = {
    "config": "264fea34b29e8dbe52f5b5e8652816a47a013c039ff94cf4f018395ebc3c887c",
    "incumbent": "bd1d45a446dc2db337c584d29372a1ac87244bd89132519f1c6a661036fd4ca3",
    "parameter_optimizer_round9": "2d15147cc0a09dbc205e9a2d8056c3f03a996bdbdbe1b803f995244559b93fe4",
    "plan_resource_estimator": "33fd489055dce710495f03236f4d38ec1010523f9c81a88b40a978bb3622a150",
    "query_snapshot": "e3a10b43cd34b7a15e5955d52cf559b204b1d9d1a2f94b83b4a27ef3c80c3b0a",
    "ratio_scale_resource_estimator": "d9485bfe5b1f394b0da01963f147df54f2f798e6e04d650f6cadd385dc578ca7",
    "selection": "cbf4f9703d56e6d45fa275815061fdb5c1b84cb50fe45dc6617115c572970646",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corrected_install_bundle_source(pending_module: Any) -> str:
    """Return the pinned two-edit v39 installer or fail closed."""

    source_path = Path(str(pending_module.__file__ or "")).resolve()
    if not source_path.is_file() or sha256(source_path) != EXPECTED_V39_SHA256:
        raise RuntimeError("XP v41 v39 pending-trust source SHA mismatch")
    source = inspect.getsource(pending_module.install_bundle)
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != EXPECTED_INSTALL_BUNDLE_SOURCE_SHA256:
        raise RuntimeError("XP v41 v39 install_bundle source SHA mismatch")
    if source.count(BROKEN_LOOP) != 1 or source.count(RETURN_BLOCK) != 1:
        raise RuntimeError("XP v41 v39 exclusion source block mismatch")
    corrected = source.replace(BROKEN_LOOP, FIXED_LOOP, 1)
    corrected = corrected.replace(RETURN_BLOCK, TYPE_SAFE_RETURN_BLOCK, 1)
    if BROKEN_LOOP in corrected or corrected.count(FIXED_LOOP) != 1:
        raise RuntimeError("XP v41 exclusion transformation failed")
    return corrected


def install_pending_bundle(bundle_module: Any, pending_module: Any) -> Any:
    """Install v39 exact-point logic with excluded IDs kept as strings."""

    source = corrected_install_bundle_source(pending_module)
    namespace = dict(pending_module.__dict__)
    exec(compile(source, "<xp_v41_type_safe_v39_install_bundle>", "exec"), namespace)
    namespace["install_bundle"](bundle_module)
    bundle_module.XP_V41_EXCLUDED_IDS_CONTRACT = CONTRACT
    return bundle_module


def validate_sealed_pool(path: Optional[Path] = None) -> dict[str, Any]:
    """Validate the exact completed cont006 EXPLAIN-only authority."""

    path = POOL if path is None else path
    if not path.is_file() or sha256(path) != EXPECTED_POOL_SHA256:
        raise RuntimeError("XP v41 sealed cont006 pool SHA mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    guard = payload.get("runtime_guard") or {}
    if (
        payload.get("contract") != "xp_round10_explain_only_candidate_generation_v1"
        or payload.get("status") != "ready"
        or payload.get("explain_only") is not True
        or int(payload.get("subset_count", -1)) != 63
        or int(payload.get("successful_subset_count", -1)) != 63
        or payload.get("workload_executed") is not False
        or payload.get("warmup_executed") is not False
        or payload.get("baseline_workload_executed") is not False
        or payload.get("benchmark_executed") is not False
        or payload.get("subprocess_used") is not False
        or guard.get("workload_executed") is not False
        or guard.get("warmup_executed") is not False
        or guard.get("baseline_workload_executed") is not False
        or guard.get("benchmark_executed") is not False
        or guard.get("subprocess_used") is not False
        or int(guard.get("rejected_count", -1)) != 0
        or int(payload.get("predicted_safe_novel_fingerprint_count", -1)) < 5
        or len(payload.get("all_candidate_rankings") or []) != 707
        or len(payload.get("candidate_rankings") or []) != 168
        or payload.get("formal_sources") != EXPECTED_FORMAL_SOURCES
    ):
        raise RuntimeError("XP v41 sealed cont006 pool contract mismatch")
    return payload


def install_pool_resume(explain_module: Any) -> Any:
    """Reuse only the exact cont006 pool; every other call stays original."""

    original = explain_module.generate_explain_only_candidates

    def generate_explain_only_candidates(*args: Any, **kwargs: Any) -> dict[str, Any]:
        output_value = kwargs.get("output_dir")
        if output_value is None:
            return original(*args, **kwargs)
        output_dir = Path(str(output_value)).resolve()
        expected_output = POOL.parent.resolve()
        if output_dir != expected_output:
            return original(*args, **kwargs)
        if args:
            raise RuntimeError("XP v41 sealed pool resume requires keyword arguments")
        incumbent_path = Path(str(kwargs.get("incumbent_path") or "")).resolve()
        config_path = Path(str(kwargs.get("config_path") or "")).resolve()
        selection_path = Path(str(kwargs.get("selection_path") or "")).resolve()
        query_path = Path(str(kwargs.get("query_snapshot_path") or "")).resolve()
        formal_root = Path(str(kwargs.get("formal_root") or "")).resolve()
        parameter_optimizer_path = formal_root / "parameter_optimizer_round9.py"
        if not parameter_optimizer_path.is_file():
            parameter_optimizer_path = formal_root.parent / "parameter_optimizer_round9.py"
        sources = {
            "config": config_path,
            "incumbent": incumbent_path,
            "selection": selection_path,
            "query_snapshot": query_path,
            "parameter_optimizer_round9": parameter_optimizer_path,
            "plan_resource_estimator": formal_root / "plan_resource_estimator.py",
            "ratio_scale_resource_estimator": formal_root / "ratio_scale_resource_estimator.py",
        }
        for name, expected in EXPECTED_FORMAL_SOURCES.items():
            path = sources[name]
            if not path.is_file() or sha256(path) != expected:
                raise RuntimeError(f"XP v41 sealed pool input SHA mismatch: {name}")
        payload = validate_sealed_pool()
        audit_path = CAMPAIGN / "batches/cont006/v41_sealed_pool_resume_audit.json"
        if audit_path.exists():
            existing = json.loads(audit_path.read_text(encoding="utf-8"))
            if existing.get("pool_sha256") != EXPECTED_POOL_SHA256:
                raise RuntimeError("XP v41 existing pool resume audit mismatch")
        else:
            audit = {
                "contract": CONTRACT,
                "pool": str(POOL.resolve()),
                "pool_sha256": EXPECTED_POOL_SHA256,
                "subset_count": 63,
                "successful_subset_count": 63,
                "workload_calls_added": 0,
                "explain_calls_repeated": 0,
                "manual_candidate_injection": False,
                "formal_state_mutation": False,
            }
            audit_path.write_text(
                json.dumps(audit, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return payload

    explain_module.generate_explain_only_candidates = generate_explain_only_candidates
    explain_module.XP_V41_SEALED_POOL_RESUME_CONTRACT = CONTRACT
    return explain_module

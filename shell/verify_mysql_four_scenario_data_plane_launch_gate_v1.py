#!/usr/bin/env python3
"""Verify the four-scenario offline data-plane launch gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import sys
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
PLAN = (
    ROOT
    / "preflight/mysql_four_scenario_data_plane_launch_gate_v1_20260731.json"
)
SCENARIOS = ("job", "tpch", "xp", "tpcc")
PG_CPUSET_NODE02 = "12-17,48-53"
EXPECTED_SEARCH = {
    "fixed_real_replay_top_k": 5,
    "job_required_nonempty_subsets": 15,
    "original_gradient_only": True,
    "qps_resource_input_prohibited": True,
    "tpcc_required_nonempty_subsets": 15,
    "tpch_required_nonempty_subsets": 15,
    "xp_required_nonempty_subsets": 127,
}
CONTRACTS = {
    "job": (
        "distributed_runtime_contract.json",
        "resource_envelope",
    ),
    "tpch": (
        "distributed_runtime_contract.json",
        "resource_envelope",
    ),
    "xp": (
        "xp_executable_launch_contract_v3.json",
        "database_resource_envelope",
    ),
    "tpcc": (
        "distributed_runtime_contract.json",
        "resource_envelope",
    ),
}


class VerifyError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise VerifyError("absent or symlinked: " + str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerifyError("invalid JSON: " + str(path)) from exc
    if not isinstance(value, dict):
        raise VerifyError("JSON object required: " + str(path))
    return value


def cpuset(value: str) -> set[int]:
    result: set[int] = set()
    for part in value.split(","):
        if "-" in part:
            start, end = (int(item) for item in part.split("-", 1))
            if start > end:
                raise VerifyError("descending CPU range")
            result.update(range(start, end + 1))
        else:
            result.add(int(part))
    if not result:
        raise VerifyError("empty CPU set")
    return result


def verify_manifest(
    path: Path,
    expected_id: str,
    expected_sha: str,
    *,
    require_main_bundle_scope: bool = True,
) -> dict[str, Any]:
    manifest = load(path)
    unsigned = dict(manifest)
    identity = unsigned.pop("bundle_id", None)
    sidecar = Path(str(path) + ".sha256")
    if (
        path.parent.name != expected_id
        or identity != expected_id
        or sha256(path) != expected_sha
        or hashlib.sha256(canonical(unsigned)).hexdigest() != expected_id
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != expected_sha + "  bundle_manifest.json\n"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise VerifyError("bundle identity/scope differs: " + str(path))
    if require_main_bundle_scope and (
        manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise VerifyError("main bundle scope differs: " + str(path))
    expected_files = manifest.get("files")
    if not isinstance(expected_files, dict) or not expected_files:
        raise VerifyError("manifest files map absent")
    actual: dict[str, str] = {}
    for candidate in sorted(path.parent.rglob("*")):
        relative = candidate.relative_to(path.parent).as_posix()
        mode = candidate.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise VerifyError("special bundle entry: " + relative)
        if candidate in (path, sidecar):
            continue
        if "__pycache__" in candidate.parts or candidate.suffix in (".pyc", ".pyo"):
            raise VerifyError("bytecode/cache in bundle: " + relative)
        actual[relative] = sha256(candidate)
    if actual != expected_files:
        raise VerifyError("exact bundle closure differs: " + str(path.parent))
    return manifest


def verify() -> dict[str, Any]:
    plan = load(PLAN)
    if (
        plan.get("schema_version")
        != "mysql_four_scenario_data_plane_launch_gate_v1"
        or plan.get("status")
        != "OFFLINE_READY_AWAITING_EXPLICIT_DATA_PLANE_START_AUTHORIZATION"
        or plan.get("formal_scope") != "mysql_only"
        or plan.get("cross_engine_comparison_prohibited") is not True
        or plan.get("postgresql_mutation_prohibited") is not True
        or plan.get("data_plane_start_authorized") is not False
        or plan.get("formal_launch_authorized") is not False
        or plan.get("accepted_round_credit") != 0
        or plan.get("jit_materials_issued") is not False
        or plan.get("search_contract") != EXPECTED_SEARCH
        or plan.get("workload_pids")
        != {"job": None, "tpcc": None, "tpch": None, "xp": None}
    ):
        raise VerifyError("launch gate scope/state differs")

    receipt_ref = plan.get("publication_receipt") or {}
    receipt_path = Path(receipt_ref.get("path") or "").resolve(strict=True)
    receipt = load(receipt_path)
    if (
        sha256(receipt_path) != receipt_ref.get("sha256")
        or receipt.get("status") != receipt_ref.get("status")
        or receipt.get("data_plane_started") is not False
        or receipt.get("postgresql_mutation_performed") is not False
        or len(receipt.get("publications") or []) != 4
        or any(
            item.get("status") != "PASS"
            or item.get("visible_verification") != "REMOTE_BUNDLE_EXACT_PASS"
            for item in receipt["publications"]
        )
    ):
        raise VerifyError("publication receipt differs")

    main_bundles = plan.get("main_bundles") or {}
    if set(main_bundles) != set(SCENARIOS):
        raise VerifyError("main bundle set differs")
    manifests: dict[str, dict[str, Any]] = {}
    bundle_roots: dict[str, Path] = {}
    for scenario in SCENARIOS:
        item = main_bundles[scenario]
        manifest_path = Path(item["local_manifest"]).resolve(strict=True)
        manifest = verify_manifest(
            manifest_path, item["bundle_id"], item["manifest_sha256"]
        )
        if (
            item.get("remote_exact_publication_verified") is not True
            or not item["remote_bundle"].endswith("/" + item["bundle_id"])
        ):
            raise VerifyError("remote bundle binding differs: " + scenario)
        manifests[scenario] = manifest
        bundle_roots[scenario] = manifest_path.parent

    issuers = plan.get("issuers") or {}
    if set(issuers) != set(SCENARIOS):
        raise VerifyError("issuer set differs")
    for scenario in SCENARIOS:
        issuer = issuers[scenario]
        entrypoint = Path(issuer["entrypoint"]).resolve(strict=True)
        if (
            issuer.get("bundle_id") != main_bundles[scenario]["bundle_id"]
            or sha256(entrypoint) != issuer.get("entrypoint_sha256")
            or issuer.get("issuer_tests_passed", True) is not True
        ):
            raise VerifyError("issuer binding differs: " + scenario)
    tpch = issuers["tpch"]
    for path_key, hash_key in (
        ("base_renderer", "base_renderer_sha256"),
        ("renderer", "renderer_sha256"),
        ("systemd_oneshot_materializer", "systemd_oneshot_materializer_sha256"),
    ):
        path = Path(tpch[path_key]).resolve(strict=True)
        if sha256(path) != tpch[hash_key]:
            raise VerifyError("TPC-H issuer component differs: " + path_key)
    if tpch.get("ephemeral_issuer_tests_passed") is not True:
        raise VerifyError("TPC-H issuer test status differs")
    xp_issuer_manifest = Path(
        issuers["xp"]["issuer_bundle_manifest"]
    ).resolve(strict=True)
    xp_issuer = verify_manifest(
        xp_issuer_manifest,
        issuers["xp"]["issuer_bundle_id"],
        issuers["xp"]["issuer_bundle_manifest_sha256"],
        require_main_bundle_scope=False,
    )
    if (
        xp_issuer.get("schema_version") != "mysql_xp_v9_jit_issuer_bundle_v1"
        or (xp_issuer.get("gate_binding") or {}).get("formal_scope")
        != "mysql_only"
        or xp_issuer.get("formal_r0_authorized") is not False
        or xp_issuer.get("formal_attempt_generated_by_build") is not False
        or xp_issuer.get("v9_bundle_id") != main_bundles["xp"]["bundle_id"]
    ):
        raise VerifyError("XP issuer bundle scope differs")

    allowed = {
        Path(item["entrypoint"]).resolve(strict=True)
        for item in issuers.values()
    }
    prohibited = {
        Path(item).resolve(strict=True)
        for item in plan.get("prohibited_stale_entrypoints") or []
    }
    if allowed & prohibited or len(prohibited) != 4:
        raise VerifyError("stale issuer prohibition differs")

    lanes = plan.get("resource_lanes") or {}
    if set(lanes) != set(SCENARIOS):
        raise VerifyError("resource lane set differs")
    used_cpus: dict[str, set[int]] = {}
    used_devices: dict[str, set[str]] = {}
    used_ports: dict[str, set[int]] = {}
    pg_cpus = cpuset(PG_CPUSET_NODE02)
    for scenario in SCENARIOS:
        lane = lanes[scenario]
        host = lane["host"]
        database_cpus = cpuset(lane["database_cpuset"])
        client_cpus = cpuset(lane["client_cpuset"])
        scenario_cpus = database_cpus | client_cpus
        if database_cpus & client_cpus:
            raise VerifyError("database/client CPUs overlap: " + scenario)
        if used_cpus.setdefault(host, set()) & scenario_cpus:
            raise VerifyError("same-host scenario CPUs overlap: " + scenario)
        used_cpus[host].update(scenario_cpus)
        if host.endswith("192.0.2.4") and scenario_cpus & pg_cpus:
            raise VerifyError("MySQL/PG CPUs overlap: " + scenario)
        device = lane["device_major_minor"]
        port = lane["mysql_port"]
        if (
            device in used_devices.setdefault(host, set())
            or port in used_ports.setdefault(host, set())
        ):
            raise VerifyError("same-host device/port overlap: " + scenario)
        used_devices[host].add(device)
        used_ports[host].add(port)

        relative, envelope_key = CONTRACTS[scenario]
        contract = load(bundle_roots[scenario] / relative)
        actual_envelope = contract.get(envelope_key) or {}
        expected_envelope = lane["resource_envelope"]
        for key, expected in expected_envelope.items():
            if actual_envelope.get(key) != expected:
                raise VerifyError(
                    f"resource envelope differs: {scenario}.{key}"
                )

    return {
        "schema_version": "mysql_four_scenario_data_plane_launch_gate_verify_v1",
        "status": "PASS_OFFLINE_LAUNCH_GATE_AWAITING_START_AUTHORIZATION",
        "plan": str(PLAN),
        "plan_sha256": sha256(PLAN),
        "main_bundle_exact_closure_count": 4,
        "issuer_binding_count": 4,
        "same_host_cpu_overlap": False,
        "postgresql_cpu_overlap": False,
        "same_host_device_overlap": False,
        "same_host_port_overlap": False,
        "jit_materials_issued": False,
        "data_plane_started": False,
        "formal_launch_authorized": False,
        "accepted_round_credit": 0,
    }


def main() -> int:
    print(json.dumps(verify(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerifyError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

#!/usr/bin/env python3
"""Build immutable, evidence-bound TPC-C v3 phase authorities offline.

This is intentionally a *post-capture* promotion helper, not a launcher.  It
does not contact a host, start a process, materialize a workload command, or
increment an accepted-round counter.  Each authority remains bound to the
qualification/capture successor bundle and the same already-observed live DB
identity.  The three phases are deliberately separate:

``fresh-r0`` requires the completed qualification + typed capture ledger;
``debug`` additionally requires the completed fresh-R0 ledger and its prior
authority; ``formal10`` additionally requires the completed debug ledger and
its prior authority.  Therefore a capture-only successor cannot be toggled to
formal by editing a flag or inventing a JSON document.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_tpcc_v3_phase_authorities"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")

PHASES = {
    "fresh-r0": {
        "phase": "fresh_r0",
        "schema": "mysql_tpcc_fresh_r0_authority_v1",
        "ledger": ("qualification", "typed_capture"),
        "prior": None,
    },
    "debug": {
        "phase": "debug",
        "schema": "mysql_tpcc_debug_authority_v1",
        "ledger": ("qualification", "typed_capture", "fresh_r0"),
        "prior": "mysql_tpcc_fresh_r0_authority_v1",
    },
    "formal10": {
        "phase": "formal10_launch",
        "schema": "mysql_tpcc_formal10_launch_authority_v1",
        "ledger": ("qualification", "typed_capture", "fresh_r0", "debug"),
        "prior": "mysql_tpcc_debug_authority_v1",
    },
}


class AuthorityError(RuntimeError):
    """Raised when a required real evidence item is absent or inconsistent."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def canonical_identity(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AuthorityError(f"regular JSON file required: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthorityError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise AuthorityError(f"JSON object required: {path}")
    return value


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AuthorityError(message)


def _readiness_module(bundle: Path):
    path = bundle / "authority/tpcc_v3_formal10_readiness_verifier.py"
    if not path.is_file() or path.is_symlink():
        raise AuthorityError("sealed TPC-C readiness verifier is absent")
    spec = importlib.util.spec_from_file_location("sealed_tpcc_v3_readiness", path)
    if spec is None or spec.loader is None:
        raise AuthorityError("sealed TPC-C readiness verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _materializer_module(bundle: Path):
    path = bundle / "authority/mysql_tpcc_materialize_phase_command_v2.py"
    if not path.is_file() or path.is_symlink():
        raise AuthorityError("sealed TPC-C phase materializer is absent")
    spec = importlib.util.spec_from_file_location("sealed_tpcc_v3_phase_materializer", path)
    if spec is None or spec.loader is None:
        raise AuthorityError("sealed TPC-C phase materializer cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_base(bundle: Path) -> tuple[dict[str, Any], dict[str, Any], Any, Any]:
    bundle = bundle.resolve(strict=True)
    verifier = _readiness_module(bundle)
    materializer = _materializer_module(bundle)
    try:
        manifest, runtime = verifier.verify_successor_content(bundle)
    except Exception as exc:
        raise AuthorityError(f"base successor closure differs: {exc}") from exc
    capabilities = runtime.get("runtime_capabilities") or {}
    expect(manifest.get("formal_scope") == "mysql_only", "base scope differs")
    expect(manifest.get("cross_engine_comparison_prohibited") is True,
           "base comparison prohibition differs")
    # The v2 successor must remain capture-only; promotion is external and
    # evidence-bound, never a mutable flag change to this immutable bundle.
    expect(manifest.get("formal_launch_authorized") is False,
           "base successor must remain capture-only")
    expect(all(capabilities.get(name) is False for name in
               ("fresh_r0", "debug", "formal_outer")),
           "base successor runtime is not the expected capture-only v2")
    return manifest, runtime, verifier, materializer


def token_from(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise AuthorityError("regular handoff token file required")
    token = path.read_text(encoding="utf-8").strip()
    expect(TOKEN_RE.fullmatch(token) is not None,
           "handoff token must be 16..128 safe characters")
    return token


def verify_service_and_prefault(
    materializer: Any,
    *,
    service_path: Path,
    prefault_path: Path,
    bundle: Path,
    token: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    service = load(service_path)
    prefault = load(prefault_path)
    try:
        materializer.validate_service(service, bundle, token)
        materializer.validate_prefault(prefault, service)
    except Exception as exc:
        raise AuthorityError(f"live service/prefault authority differs: {exc}") from exc
    return service, prefault


def verify_registry(verifier: Any, registry: Path) -> dict[str, Any]:
    try:
        return verifier.validate_production_registry(registry)
    except Exception as exc:
        raise AuthorityError(f"real typed production registry differs: {exc}") from exc


def _zero_io(value: Mapping[str, Any]) -> bool:
    return value.get("client_target_device_io_zero_verified") is True and value.get(
        "client_target_device_io_delta"
    ) == {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0,
          "dbytes": 0, "dios": 0}


def verify_handoff_prefix(
    handoff_path: Path,
    *,
    phase_key: str,
    bundle_id: str,
    service: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    handoff = load(handoff_path)
    spec = PHASES[phase_key]
    expected = list(spec["ledger"])
    expect(handoff.get("schema_version") ==
           "mysql_tpcc_same_live_container_formal10_handoff_v2",
           "same-live handoff schema differs")
    expected_status = "PRELAUNCH_READY" if phase_key == "formal10" else "INCOMPLETE"
    expect(handoff.get("status") == expected_status,
           "handoff status does not match completed phase prefix")
    expect(handoff.get("formal_scope") == "mysql_only" and
           handoff.get("cross_engine_comparison_prohibited") is True,
           "handoff scope differs")
    expect(handoff.get("bundle_id") == bundle_id and
           handoff.get("attempt_id") == service.get("attempt_id"),
           "handoff bundle/attempt differs")
    database = service["database"]
    provenance = registry["production_trace_provenance"]
    identity = ("container_id", "pid", "process_start_ticks", "datadir")
    for field in identity:
        handoff_key = "db_" + field
        expect(handoff.get(handoff_key) == database.get(field),
               f"handoff database {field} differs")
        registry_key = "db_" + field
        expect(handoff.get(handoff_key) == provenance.get(registry_key),
               f"handoff does not bind registry database {field}")
    expect(handoff.get("container_recreated") is False and
           handoff.get("datadir_replaced") is False and
           handoff.get("database_restarted") is False,
           "handoff continuity is broken")
    phases = handoff.get("phases")
    expect(isinstance(phases, Mapping) and set(phases) == set(expected),
           "handoff phase set differs")
    timestamps: list[int] = []
    validated: dict[str, Mapping[str, Any]] = {}
    for name in expected:
        value = phases[name]
        expect(isinstance(value, Mapping) and value.get("status") == "PASS",
               f"handoff phase did not pass: {name}")
        expect(value.get("attempt_id") == service.get("attempt_id") and
               value.get("bundle_id") == bundle_id,
               f"handoff phase identity differs: {name}")
        for field in identity:
            expect(value.get("db_" + field) == database.get(field),
                   f"handoff phase database differs: {name}.{field}")
        expect(_zero_io(value), f"handoff client target-device IO differs: {name}")
        observed = value.get("observed_epoch_ms")
        expect(isinstance(observed, int) and observed > 0,
               f"handoff phase timestamp differs: {name}")
        timestamps.append(observed)
        validated[name] = value
    # JSON object key order cannot be authority: the sealed writer uses
    # sort_keys=True.  The only durable order is the strictly increasing
    # observed epoch, which is checked against the fixed phase sequence.
    ordered = [name for name, _value in sorted(
        validated.items(), key=lambda item: item[1]["observed_epoch_ms"]
    )]
    expect(ordered == expected and len(set(timestamps)) == len(timestamps),
           "handoff phase timestamp order differs")
    return handoff


def verify_prior_authority(
    path: Path | None,
    *,
    expected_schema: str | None,
    phase_key: str,
    bundle_id: str,
    service: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    if expected_schema is None:
        expect(path is None, "fresh-r0 must not consume a prior phase authority")
        return None, None
    expect(path is not None, f"{phase_key} requires prior phase authority")
    prior = load(path)
    authority = prior.get("phase_authority")
    enclosing: Mapping[str, Any] | None = prior if isinstance(authority, Mapping) else None
    if not isinstance(authority, Mapping):
        # Operators pass the sealed phase_authority.json to the materializer,
        # while this offline builder needs its enclosing content ID too.
        authority = prior
        manifest_path = path.parent / "bundle_manifest.json"
        enclosing = load(manifest_path)
        expect(enclosing.get("phase_authority") == authority,
               "prior authority bundle payload differs")
    expect(isinstance(authority, Mapping), "prior authority payload is absent")
    expect(authority.get("schema_version") == expected_schema and
           authority.get("status") == "PASS", "prior authority schema/status differs")
    expect(authority.get("bundle_id") == bundle_id and
           authority.get("attempt_id") == service.get("attempt_id"),
           "prior authority bundle/attempt differs")
    expect(authority.get("handoff_token_sha256") == service.get("handoff_token_sha256"),
           "prior authority token differs")
    expect(authority.get("db_container_id") == service["database"]["container_id"] and
           authority.get("db_pid") == service["database"]["pid"] and
           authority.get("db_process_start_ticks") == service["database"]["process_start_ticks"],
           "prior authority live database differs")
    expected_previous = PHASES[phase_key]["ledger"][-1]
    expect(authority.get("authorized_phase") == expected_previous,
           "prior authority phase differs")
    # An authority alone is not proof of execution.  The ledger has to include
    # the prior phase with a real zero-I/O window.
    expect(expected_previous in (handoff.get("phases") or {}),
           "prior authority has no completed handoff phase")
    expect(enclosing is not None and enclosing.get("bundle_id") ==
           enclosing.get("content_id") == path.parent.name,
           "prior authority content identity differs")
    return prior, sha256(path)


def _copy_input(stage: Path, name: str, path: Path) -> str:
    destination = stage / "evidence" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)
    return sha256(destination)


def seal(
    *,
    phase_key: str,
    base_bundle: Path,
    registry: Path,
    service: Path,
    prefault: Path,
    handoff: Path,
    token_file: Path,
    prior_authority: Path | None,
    output_root: Path,
) -> Path:
    base_manifest, _runtime, verifier, materializer = verify_base(base_bundle)
    base_bundle = base_bundle.resolve(strict=True)
    token = token_from(token_file)
    live_service, prefault_value = verify_service_and_prefault(
        materializer, service_path=service, prefault_path=prefault,
        bundle=base_bundle, token=token,
    )
    registry_manifest = verify_registry(verifier, registry)
    live_handoff = verify_handoff_prefix(
        handoff, phase_key=phase_key, bundle_id=base_bundle.name,
        service=live_service, registry=registry_manifest,
    )
    prior, prior_sha = verify_prior_authority(
        prior_authority, expected_schema=PHASES[phase_key]["prior"],
        phase_key=phase_key, bundle_id=base_bundle.name, service=live_service,
        handoff=live_handoff,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tpcc-phase-authority.", dir=output_root))
    try:
        input_paths = {
            "registry_manifest.json": registry / "bundle_manifest.json",
            "service.json": service,
            "prefault.json": prefault,
            "handoff.json": handoff,
        }
        if prior_authority is not None:
            input_paths["prior_authority.json"] = prior_authority
        copied = {name: _copy_input(stage, name, path) for name, path in input_paths.items()}
        database = live_service["database"]
        authority = {
            "schema_version": PHASES[phase_key]["schema"],
            "status": "PASS",
            "phase": PHASES[phase_key]["phase"],
            "authorized_phase": PHASES[phase_key]["phase"],
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "bundle_id": base_bundle.name,
            "attempt_id": token,
            "handoff_token_sha256": live_service["handoff_token_sha256"],
            "db_container_id": database["container_id"],
            "db_pid": database["pid"],
            "db_process_start_ticks": database["process_start_ticks"],
            "db_datadir": database["datadir"],
            "db_device_major_minor": database["device_major_minor"],
            "db_cpuset_cpus": database["cpuset_cpus"],
            "db_cpuset_mems": database["cpuset_mems"],
            "mysql_port": database["mysql_port"],
            "registry_bundle_id": registry_manifest["bundle_id"],
            "registry_manifest_sha256": copied["registry_manifest.json"],
            "service_sha256": copied["service.json"],
            "prefault_sha256": copied["prefault.json"],
            "handoff_sha256": copied["handoff.json"],
            "prior_authority_sha256": prior_sha,
            "requires_real_production_trace": True,
            "requires_dynamic_gate_pass": True,
            "requires_same_live_container_handoff": True,
            "requires_client_target_device_zero_io": True,
            "formal_launch_authorized": phase_key == "formal10",
            "formal_r0_authorized": phase_key == "fresh-r0",
            "accepted_round_authorized": False,
            "qps_resource_input_prohibited": True,
        }
        files = {"evidence/" + name: digest for name, digest in copied.items()}
        files["phase_authority.json"] = sha_bytes(canonical(authority))
        unsigned = {
            "schema_version": "mysql_tpcc_v2_phase_authority_bundle_v1",
            "immutable": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "scenario": "tpcc",
            "phase_key": phase_key,
            "base_capture_successor_id": base_bundle.name,
            "real_production_trace_registry_id": registry_manifest["bundle_id"],
            "phase_authority_schema": authority["schema_version"],
            "phase_authority": authority,
            "input_hashes": copied,
            "files": files,
            "accepted_round_authorized": False,
        }
        content_id = sha_bytes(canonical_identity(unsigned))
        manifest = {**unsigned, "bundle_id": content_id, "content_id": content_id}
        (stage / "phase_authority.json").write_bytes(canonical(authority))
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            f"{sha256(stage / 'bundle_manifest.json')}  bundle_manifest.json\n",
            encoding="ascii",
        )
        for path in stage.rglob("*"):
            if path.is_file():
                path.chmod(0o400)
            elif path.is_dir():
                path.chmod(0o500)
        destination = output_root / content_id
        if destination.exists():
            verify(destination)
            shutil.rmtree(stage)
            return destination
        os.replace(stage, destination)
        return destination
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest = load(bundle / "bundle_manifest.json")
    unsigned = dict(manifest)
    content_id = unsigned.pop("content_id", None)
    bundle_id = unsigned.pop("bundle_id", None)
    expect(bundle_id == content_id == bundle.name and SHA_RE.fullmatch(bundle.name) is not None,
           "authority bundle identity differs")
    expect(sha_bytes(canonical_identity(unsigned)) == bundle.name,
           "authority bundle content identity differs")
    expect(manifest.get("schema_version") == "mysql_tpcc_v2_phase_authority_bundle_v1" and
           manifest.get("immutable") is True and
           manifest.get("formal_scope") == "mysql_only" and
           manifest.get("cross_engine_comparison_prohibited") is True,
           "authority bundle scope differs")
    files = manifest.get("files")
    expect(isinstance(files, Mapping) and files, "authority closure is absent")
    for relative, expected in files.items():
        path = bundle / str(relative)
        expect(path.is_file() and not path.is_symlink() and sha256(path) == expected,
               f"authority closure differs: {relative}")
    authority = load(bundle / "phase_authority.json")
    expect(authority == manifest.get("phase_authority"),
           "manifest/authority payload differs")
    expect(authority.get("accepted_round_authorized") is False and
           authority.get("qps_resource_input_prohibited") is True,
           "authority counting/QPS policy differs")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seal", "verify"))
    parser.add_argument("--phase", choices=tuple(PHASES))
    parser.add_argument("--base-bundle", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--service", type=Path)
    parser.add_argument("--prefault", type=Path)
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--prior-authority", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            if args.bundle is None:
                raise AuthorityError("verify requires --bundle")
            value = verify(args.bundle)
            print(json.dumps({"status": "PASS", "bundle_id": value["bundle_id"]}, sort_keys=True))
            return 0
        required = (args.phase, args.base_bundle, args.registry, args.service,
                    args.prefault, args.handoff, args.token_file)
        if any(value is None for value in required):
            raise AuthorityError("seal lacks required evidence argument")
        bundle = seal(phase_key=args.phase, base_bundle=args.base_bundle,
                      registry=args.registry, service=args.service,
                      prefault=args.prefault, handoff=args.handoff,
                      token_file=args.token_file,
                      prior_authority=args.prior_authority,
                      output_root=args.output_root)
        print(json.dumps({"status": "PASS", "bundle": str(bundle)}, sort_keys=True))
        return 0
    except (AuthorityError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

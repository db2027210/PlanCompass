#!/usr/bin/env python3
"""Build a TPC-H successor that verifies the live daemon affinity remediation.

The transient systemd unit still declares 34-35,70-71.  A separately sealed,
same-live-process authority records the effective 32-33,68-69 affinity that
keeps the TPC-H and TPC-C Docker daemons disjoint.  This offline transmuter
teaches both bootstrap and qualification readback to validate that authority;
it does not mutate either daemon or authorize a formal/accepted run.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "94beaa70a6c2f26c174c835a75a0e758bd42bd0bc776cca07d75785cf08b44ed"
PARENT_MANIFEST_SHA256 = "57f14b2e0ea2a439236dbf1a4318d8679bce56b21abe7815f5d0192ab46448a2"
PARENT = ROOT / "artifacts/mysql_tpch_observed_recalibration_successor_v1" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_affinity_remediation_successor_v1"
SOURCE_AUTHORITY = Path("/tmp/tpch_daemon_affinity_97636_233390522_v1.json")
AUTHORITY_SHA256 = "c6906e3580f88b6e21fcaa41250b51cc76952656e7a20fe08efed4370ce0e853"
AUTHORITY_INTERNAL_SHA256 = "915d27c99be693866bb31f0260727e2012bf58351bd7d9d5da9a8cdb4433528c"
AUTHORITY_RELATIVE = "authority/tpch_daemon_affinity_remediation_v1.json"
AUTHORITY_SIDECAR_RELATIVE = AUTHORITY_RELATIVE + ".sha256"
BUILDER_RELATIVE = "builder/build_mysql_tpch_affinity_remediation_successor_v1.py"
INVALIDATION_RELATIVE = "invalidated_19ac_affinity_unaware_attempt.json"
BOOTSTRAP = "mysql_tpch_distributed_v2_authority.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
FORMAL = "tpch_formal10_contract.json"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(label + " is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BuildError(label + " is not a JSON object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink is prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
    return result


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    sidecar = PARENT / "bundle_manifest.json.sha256"
    manifest = load_object(manifest_path.read_bytes(), "parent manifest")
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if (
        PARENT.name != PARENT_ID
        or manifest.get("bundle_id") != PARENT_ID
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or sidecar.read_text(encoding="ascii")
        != PARENT_MANIFEST_SHA256 + "  bundle_manifest.json\n"
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, or authority differs")
    return manifest


def replace_once(payload: bytes, old: str, new: str, label: str) -> bytes:
    text = payload.decode("utf-8")
    if text.count(old) != 1:
        raise BuildError(label + ": patch point cardinality differs")
    return text.replace(old, new).encode("utf-8")


def replace_function(payload: bytes, start: str, end: str, replacement: str,
                     label: str) -> bytes:
    text = payload.decode("utf-8")
    if text.count(start) != 1 or text.count(end) != 1:
        raise BuildError(label + ": function boundary cardinality differs")
    left = text.index(start)
    right = text.index(end, left)
    return (text[:left] + replacement.rstrip() + "\n\n" + text[right:]).encode("utf-8")


def verifier_code(error_name: str, run_name: str, load_name: str,
                  sha_name: str, canonical_name: str) -> str:
    start_ticks_name = (
        "_proc_starttime_ticks"
        if error_name == "AuthorityError"
        else "process_start_ticks"
    )
    return f'''def _verify_live_daemon_affinity_remediation(bundle, contract, pid):
    path = Path(bundle) / {AUTHORITY_RELATIVE!r}
    sidecar = Path(str(path) + ".sha256")
    try:
        payload = path.read_bytes()
        authority = {load_name}(path, "daemon affinity remediation authority")
    except (OSError, UnicodeError, ValueError) as exc:
        raise {error_name}("daemon affinity remediation authority unreadable") from exc
    if (
        hashlib.sha256(payload).hexdigest() != {AUTHORITY_SHA256!r}
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != {AUTHORITY_SHA256!r} + "  " + path.name + "\\n"
    ):
        raise {error_name}("daemon affinity remediation file identity differs")
    unsigned = dict(authority)
    recorded = unsigned.pop("authority_sha256", None)
    expected_effective = "32-33,68-69"
    expected_peer = "34-35,70-71"
    try:
        tids = sorted(
            int(entry.name)
            for entry in (Path("/proc") / str(pid) / "task").iterdir()
            if entry.name.isdigit()
        )
        task_sets = {{tid: set(os.sched_getaffinity(tid)) for tid in tids}}
        peer_pid = int(authority.get("peer_main_pid") or 0)
        peer_tids = sorted(
            int(entry.name)
            for entry in (Path("/proc") / str(peer_pid) / "task").iterdir()
            if entry.name.isdigit()
        )
        peer_sets = {{tid: set(os.sched_getaffinity(tid)) for tid in peer_tids}}
        shown = {run_name}(
            ["systemctl", "show", str(authority.get("peer_unit") or ""),
             "--property=ActiveState", "--property=SubState", "--property=MainPID"],
            timeout=30{', label="peer daemon systemd readback"' if run_name == 'run' else ''},
        ){'' if run_name == 'run' else '.stdout'}
        properties = {{}}
        for line in shown.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                properties[key] = value
    except (OSError, PermissionError, ValueError) as exc:
        raise {error_name}("daemon affinity remediation live readback failed") from exc
    wanted = set((32, 33, 68, 69))
    peer_wanted = set((34, 35, 70, 71))
    if (
        recorded != {AUTHORITY_INTERNAL_SHA256!r}
        or recorded != hashlib.sha256({canonical_name}(unsigned)).hexdigest()
        or authority.get("schema_version")
        != "mysql_dedicated_daemon_live_affinity_remediation_v1"
        or authority.get("status") != "PASS_EFFECTIVE_DISJOINT_DAEMON_AFFINITY"
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("postgresql_mutation_prohibited") is not True
        or authority.get("unit") != contract["unit"]
        or int(authority.get("main_pid") or 0) != pid
        or int(authority.get("main_pid_startticks") or 0)
        != contract["main_pid_starttime_ticks"]
        or authority.get("cmdline_sha256") != contract["main_pid_cmdline_sha256"]
        or authority.get("declared_exec_affinity")
        != contract["main_pid_cpus_allowed_list"]
        or authority.get("effective_affinity_after") != expected_effective
        or authority.get("peer_effective_affinity") != expected_peer
        or authority.get("peer_unit") != "ra-docker-mysql-tpcc-v1.service"
        or authority.get("all_threads_exact") is not True
        or authority.get("effective_sets_disjoint") is not True
        or authority.get("container_mutation_performed") is not False
        or authority.get("storage_mutation_performed") is not False
        or authority.get("network_mutation_performed") is not False
        or authority.get("systemd_unit_mutation_performed") is not False
        or authority.get("formal_launch_authorized") is not False
        or authority.get("accepted_round_authorized") is not False
        or properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or int(properties.get("MainPID") or 0) != peer_pid
        or not task_sets
        or any(values != wanted for values in task_sets.values())
        or not peer_sets
        or any(values != peer_wanted for values in peer_sets.values())
        or any(values & peer for values in task_sets.values() for peer in peer_sets.values())
    ):
        raise {error_name}("daemon affinity remediation authority differs")
    return {{
        "file_sha256": {AUTHORITY_SHA256!r},
        "authority_sha256": recorded,
        "declared_affinity": contract["main_pid_cpus_allowed_list"],
        "effective_affinity": expected_effective,
        "all_current_threads_exact": True,
        "peer_main_pid": peer_pid,
        "peer_all_current_threads_exact": True,
        "effective_sets_disjoint": True,
    }}


def _live_daemon_process_readback(pid, contract{', bundle' if error_name == 'AuthorityError' else ''}):
    remediation = _verify_live_daemon_affinity_remediation(
        {'bundle, contract, pid' if error_name == 'AuthorityError' else 'bundle_from_script(), contract, pid'}
    )
    try:
        status_lines = (Path("/proc") / str(pid) / "status").read_text(
            encoding="ascii"
        ).splitlines()
        cmdline = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        cgroup_lines = (Path("/proc") / str(pid) / "cgroup").read_text(
            encoding="ascii"
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise {error_name}("live daemon process readback failed") from exc
    status = {{}}
    for line in status_lines:
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
    argv = [
        item.decode("utf-8")
        for item in cmdline.rstrip(b"\\0").split(b"\\0")
        if item
    ]
    namespaces = {{}}
    try:
        for name in contract["mandatory_namespaces"]:
            namespaces[name] = os.readlink("/proc/%d/ns/%s" % (pid, name))
    except OSError as exc:
        raise {error_name}("live daemon mandatory namespace readback failed") from exc
    cgroup_ns_path = "/proc/%d/ns/cgroup" % pid
    cgroup_ns_supported = os.path.lexists(cgroup_ns_path)
    if (
        int(status.get("PPid") or 0) != contract["main_pid_ppid"]
        or status.get("Cpus_allowed_list") != remediation["effective_affinity"]
        or {start_ticks_name}(pid) != contract["main_pid_starttime_ticks"]
        or hashlib.sha256(cmdline).hexdigest() != contract["main_pid_cmdline_sha256"]
        or argv != contract["main_pid_argv"]
        or cgroup_lines != contract["cgroup_v1_lines"]
        or namespaces != contract["mandatory_namespace_identities"]
        or cgroup_ns_supported != contract["optional_cgroup_namespace"]["supported"]
        or (not cgroup_ns_supported) != contract["optional_cgroup_namespace"]["path_absent"]
    ):
        raise {error_name}("live daemon process identity differs")
    return {{
        "ppid": int(status["PPid"]),
        "cpus_allowed_list": status["Cpus_allowed_list"],
        "declared_cpus_allowed_list": contract["main_pid_cpus_allowed_list"],
        "affinity_remediation": remediation,
        "starttime_ticks": contract["main_pid_starttime_ticks"],
        "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
        "argv": argv,
        "cgroup_v1_lines": cgroup_lines,
        "mandatory_namespaces": namespaces,
        "optional_cgroup_namespace": {{
            "supported": cgroup_ns_supported,
            "path_absent": not cgroup_ns_supported,
        }},
    }}'''


def patch_runtime(content: dict[str, bytes]) -> None:
    main_code = verifier_code("AuthorityError", "_run", "load_object", "sha256", "canonical")
    content[BOOTSTRAP] = replace_function(
        content[BOOTSTRAP], "def _live_daemon_process_readback(",
        "def _unix_listener_identity(", main_code, BOOTSTRAP,
    )
    content[BOOTSTRAP] = replace_once(
        content[BOOTSTRAP],
        "process_readback = _live_daemon_process_readback(\n        main_pid, contract\n    )",
        "process_readback = _live_daemon_process_readback(\n        main_pid, contract, Path(spec[\"bundle\"][\"path\"])\n    )",
        BOOTSTRAP,
    )
    collector_code = verifier_code(
        "QualificationError", "run", "load_object", "sha256", "canonical"
    )
    content[COLLECTOR] = replace_function(
        content[COLLECTOR], "def _live_daemon_process_readback(",
        "def _unix_listener_identity(", collector_code, COLLECTOR,
    )


def invalidation() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_affinity_unaware_attempt_invalidation_v1",
        "status": "INVALIDATED_DO_NOT_RERUN_OR_CREDIT",
        "attempt_token": "tpch-v2-20260731t135939z-19acbf59bbf9f4ccab2cb1ac",
        "bundle_id": PARENT_ID,
        "failure": "live daemon process identity differs",
        "cause": "bootstrap verifier compared remediated effective affinity to declared ExecStart affinity",
        "workload_started": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "replay_or_rerun_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def chmod_sealed(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            os.chmod(path, 0o444)
        elif path.is_dir():
            os.chmod(path, 0o555)
    os.chmod(root, 0o555)


def verify_published(bundle: Path, manifest: dict[str, Any]) -> None:
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if (
        bundle.name != manifest["bundle_id"]
        or sha_bytes(canonical(unsigned)) != manifest["bundle_id"]
        or relative_files(bundle) != manifest["files"]
        or (bundle / "bundle_manifest.json.sha256").read_text(encoding="ascii")
        != sha_file(bundle / "bundle_manifest.json") + "  bundle_manifest.json\n"
    ):
        raise BuildError("published identity or closure differs")
    for path in [bundle, *bundle.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable: " + str(path))


def build() -> dict[str, Any]:
    parent = verify_parent()
    authority_payload = SOURCE_AUTHORITY.read_bytes()
    authority = load_object(authority_payload, "affinity authority")
    unsigned_authority = dict(authority)
    recorded = unsigned_authority.pop("authority_sha256", None)
    if (
        sha_bytes(authority_payload) != AUTHORITY_SHA256
        or recorded != AUTHORITY_INTERNAL_SHA256
        or recorded != sha_bytes(canonical(unsigned_authority))
        or authority.get("status") != "PASS_EFFECTIVE_DISJOINT_DAEMON_AFFINITY"
        or authority.get("main_pid") != 97636
        or authority.get("main_pid_startticks") != 233390522
        or authority.get("declared_exec_affinity") != "34-35,70-71"
        or authority.get("effective_affinity_after") != "32-33,68-69"
        or authority.get("peer_effective_affinity") != "34-35,70-71"
        or authority.get("formal_launch_authorized") is not False
        or authority.get("accepted_round_authorized") is not False
    ):
        raise BuildError("affinity remediation authority identity differs")

    content: dict[str, bytes] = {}
    for relative, expected in sorted(parent["files"].items()):
        payload = (PARENT / relative).read_bytes()
        if sha_bytes(payload) != expected:
            raise BuildError("parent file differs: " + relative)
        content[relative] = payload
    patch_runtime(content)
    content[AUTHORITY_RELATIVE] = authority_payload
    content[AUTHORITY_SIDECAR_RELATIVE] = (
        AUTHORITY_SHA256 + "  " + Path(AUTHORITY_RELATIVE).name + "\n"
    ).encode("ascii")
    content[BUILDER_RELATIVE] = HERE.read_bytes()
    content[INVALIDATION_RELATIVE] = canonical(invalidation())

    component_paths = dict(parent.get("component_hashes") or {})
    reverse = {digest: relative for relative, digest in parent["files"].items()}
    rebound: dict[str, str] = {}
    for name, old_hash in component_paths.items():
        relative = reverse.get(old_hash)
        if relative is None:
            raise BuildError("parent component hash is not in closure: " + name)
        rebound[name] = sha_bytes(content[relative])
    formal = load_object(content[FORMAL], FORMAL)
    formal["qualification_hashes"] = {
        name: value for name, value in rebound.items()
        if name not in ("self_contained_read_only_verifier",
                        "systemd_oneshot_materializer")
    }
    content[FORMAL] = canonical(formal)

    files = {name: sha_bytes(payload) for name, payload in sorted(content.items())}
    identity = {key: value for key, value in parent.items()
                if key not in ("bundle_id", "files")}
    identity.update({
        "successor_generation": "live_affinity_remediation_v1",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "builder_sha256": sha_file(HERE),
        "component_hashes": rebound,
        "formal10_contract_sha256": files[FORMAL],
        "daemon_declared_affinity_preserved": "34-35,70-71",
        "daemon_effective_affinity_required": "32-33,68-69",
        "affinity_remediation_required": True,
        "affinity_remediation_authority_sha256": files[AUTHORITY_RELATIVE],
        "affinity_remediation_internal_sha256": AUTHORITY_INTERNAL_SHA256,
        "invalidated_affinity_unaware_attempt_sha256": files[INVALIDATION_RELATIVE],
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    unsigned = dict(identity)
    bundle_id = sha_bytes(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".tpch-affinity-remediation.",
                                      dir=OUTPUT_ROOT))
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            manifest_payload = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(manifest_payload)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(manifest_payload) + "  bundle_manifest.json\n",
                encoding="ascii",
            )
            chmod_sealed(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    verify_published(destination, manifest)
    return {
        "status": "PASS_IMMUTABLE_TPCH_AFFINITY_REMEDIATION_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "bundle_manifest_sha256": sha_file(destination / "bundle_manifest.json"),
        "verified_files": len(files),
        "declared_affinity": "34-35,70-71",
        "effective_affinity": "32-33,68-69",
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(build(), sort_keys=True, indent=2))
    except (BuildError, OSError, UnicodeError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)

#!/usr/bin/env python3
"""Create local-only, sidecar-bound JOB bootstrap materials for one attempt.

This never contacts node02 or executes a plan.  It binds only the final
no-PSI-capable JOB successor, emits a private token, a qualification-only
bootstrap spec, and the authority's read-only command plan.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import render_mysql_job_v32_bootstrap_spec_tmpfs_v2 as renderer
import validate_mysql_job_v32_bootstrap_spec_tmpfs_v2 as validator


ROOT = HERE.parent
BUNDLE_ID = "ea8880956d5a08edfe95a9883c2d236b105774da7a450adfae8f60e238edbfa3"
BUNDLE_MANIFEST_SHA256 = "607481ef04ba9967e3d710aa810a2c67d23538e1e7572e32d73b29e60faf2caa"
DEFAULT_BUNDLE = ROOT / "artifacts/mysql_job_v32_distributed_v2_tmpfs_successor" / BUNDLE_ID
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_job_v32_tmpfs_bootstrap_materials_v2"
MYSQL_REFERENCE = "mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
MYSQL_IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
VALID_MINUTES = 45


class MaterialError(RuntimeError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def write_sealed(path, value, mode=0o444):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise MaterialError("refusing to overwrite " + path.name)
    data = canonical(value)
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
    sidecar.write_text("%s  %s\n" % (hashlib.sha256(data).hexdigest(), path.name), encoding="ascii")
    os.chmod(path, mode)
    os.chmod(sidecar, mode)


def verify_no_psi_bundle(bundle):
    bundle = Path(bundle).resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    sidecar = Path(str(manifest_path) + ".sha256")
    if (bundle.name != BUNDLE_ID or bundle.is_symlink() or manifest_path.is_symlink()
            or sidecar.is_symlink() or not manifest_path.is_file() or not sidecar.is_file()
            or sha256(manifest_path) != BUNDLE_MANIFEST_SHA256
            or sidecar.read_text(encoding="ascii") != "%s  %s\n" % (BUNDLE_MANIFEST_SHA256, manifest_path.name)):
        raise MaterialError("final JOB bundle manifest differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if (manifest.get("bundle_id") != BUNDLE_ID or not isinstance(files, dict)
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise MaterialError("final JOB bundle authority boundary differs")
    required = (
        "distributed_runtime_contract.json",
        "qualification/mysql_job_distributed_live_readback_qualification_v2.py",
        "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
        "authority/mysql_job_v32_350a_no_psi_invalidation_20260731.json",
        "authority/mysql_job_v32_6bcd_collector_import_invalidation_20260731.json",
        "authority/mysql_job_v32_6bf_qualification_root_mount_attribution_invalidation_20260731.json",
        "authority/mysql_job_v32_a50d_measurement_host_tmpfs_invalidation_20260731.json",
        "authority/mysql_job_v32_79b2_measurement_bind_ordering_invalidation_20260731.json",
        "mysql_tpch_client_immutable_identity_v1.py",
        "qualification/mysql_tpch_client_immutable_identity_v1.py",
        "qualification/mysql_job_host_root_inventory_v1.py",
    )
    for relative in required:
        path = bundle / relative
        if path.is_symlink() or not path.is_file() or files.get(relative) != sha256(path):
            raise MaterialError("manifest closure differs: " + relative)
    runtime = json.loads((bundle / "distributed_runtime_contract.json").read_text(encoding="utf-8"))
    host_bind = runtime.get("measurement_host_bind") or {}
    if host_bind != {
        "schema_version": "mysql_job_attempt_private_host_tmpfs_bind_v1",
        "host_path_rule": "/dev/shm/ra_mysql_job_v2/<attempt_token>",
        "container_destination": "/measurement", "filesystem": "tmpfs",
        "size_bytes": 1073741824, "uid": 1008, "gid": 1008,
        "mode": "0700", "mount_options": ["rw", "nosuid", "nodev", "noexec"],
        "client_docker_tmpfs_prohibited": True,
        "bootstrap_collector_renderer_same_host_bind_required": True,
    }:
        raise MaterialError("attempt-private host tmpfs bind closure differs")
    policy = runtime.get("qualification_policy") or {}
    if (
        policy.get("cpu_contention_policy_version") != "mysql_algorithm_c_cpu_contention_signal_v2"
        or policy.get("cpu_contention_psi_preferred") is not True
        or policy.get("no_psi_cpu_fallback") != "proc_stat_busy_steal_iowait_plus_loadavg_runnable_total_v1"
        or policy.get("no_psi_memory_fallback") != "cgroup_memory_occupancy_swap_oom_v1"
        or policy.get("contention_sample_counter_monotonicity_required") is not True
    ):
        raise MaterialError("no-PSI policy closure differs")
    invalidation = json.loads((bundle / required[3]).read_text(encoding="utf-8"))
    if (
        invalidation.get("bundle_id") != "350a2b2a800a7fd4d9af032de980a4a537e51683890258587a280addd17a3ac9"
        or invalidation.get("accepted_rounds_credit") != 0
        or invalidation.get("observed", {}).get("workload_started") is not False
        or invalidation.get("formal_launch_authorized") is not False
    ):
        raise MaterialError("350a no-PSI invalidation closure differs")
    helper_root = bundle / "mysql_tpch_client_immutable_identity_v1.py"
    helper_qualification = bundle / "qualification/mysql_tpch_client_immutable_identity_v1.py"
    dependency = runtime.get("collector_dependency_closure") or {}
    import_invalidation = json.loads((bundle / required[4]).read_text(encoding="utf-8"))
    if (
        helper_root.read_bytes() != helper_qualification.read_bytes()
        or dependency.get("root_helper") != helper_root.name
        or dependency.get("qualification_helper") != "qualification/" + helper_root.name
        or dependency.get("helper_sha256") != sha256(helper_root)
        or dependency.get("root_and_qualification_bytes_identical") is not True
        or dependency.get("empty_pythonpath_subprocess_import_smoke_required") is not True
        or import_invalidation.get("bundle_id") != "6bcd66a5011081c31d8adc12372304f2ff93d511824114be06dc617ea1a6fa9f"
        or import_invalidation.get("accepted_rounds_credit") != 0
        or import_invalidation.get("workload_started") is not False
    ):
        raise MaterialError("collector helper closure/6bcd invalidation differs")
    root_mount_helper = bundle / "qualification/mysql_job_host_root_inventory_v1.py"
    root_mount_invalidation = json.loads((bundle / required[5]).read_text(encoding="utf-8"))
    root_mount_contract = dependency.get("host_root_mount_inventory_helper") or {}
    if (
        manifest.get("host_root_mount_inventory_helper_sha256")
        != sha256(root_mount_helper)
        or root_mount_contract.get("required") is not True
        or root_mount_contract.get("helper_relative")
        != "qualification/mysql_job_host_root_inventory_v1.py"
        or root_mount_contract.get("helper_sha256") != sha256(root_mount_helper)
        or root_mount_contract.get("mutation_prohibited") is not True
        or root_mount_contract.get("postgresql_read_only_inventory_allowed")
        is not True
        or root_mount_contract.get("unprivileged_inspect_exact_crosscheck_required")
        is not True
        or root_mount_invalidation.get("bundle_id")
        != "6bf0d0a613ca8e063ccfd215921aba92907a145ceb73ad1e5d11bc8cda4c48d0"
        or root_mount_invalidation.get("accepted_rounds_credit") != 0
        or root_mount_invalidation.get("workload_started") is not True
        or root_mount_invalidation.get("qualification_completed") is not False
        or root_mount_invalidation.get("formal_r0_authorized") is not False
    ):
        raise MaterialError("root mount helper/6bf invalidation closure differs")
    a50d = json.loads((bundle / required[6]).read_text(encoding="utf-8"))
    if (
        a50d.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or a50d.get("bundle_id")
        != "a50dd3633babccf609cf24c548743f3a8c7c5084f5745e7fdb508a051865bdfb"
        or a50d.get("accepted_rounds_credit") != 0
        or a50d.get("replay_or_rerun_authorized") is not False
    ):
        raise MaterialError("a50d host-tmpfs invalidation closure differs")
    invalidated_79b2 = json.loads((bundle / required[7]).read_text(encoding="utf-8"))
    if (
        invalidated_79b2.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or invalidated_79b2.get("bundle_id")
        != "79b2b0018cc4367b27515907a961a830aa9b871e8a498614ee8b541f69182bbf"
        or invalidated_79b2.get("accepted_rounds_credit") != 0
        or invalidated_79b2.get("replay_or_rerun_authorized") is not False
    ):
        raise MaterialError("79b2 measurement-bind invalidation closure differs")
    return bundle, manifest


def create(bundle, output_root):
    bundle, unused_manifest = verify_no_psi_bundle(bundle)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    attempt = "job-v2-" + now.strftime("%Y%m%dt%H%M%Sz").lower() + "-" + secrets.token_hex(12)
    if renderer.ATTEMPT.fullmatch(attempt) is None:
        raise MaterialError("generated attempt token differs")
    output = Path(output_root).resolve() / attempt
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise MaterialError("attempt output already exists")
    output.mkdir(mode=0o700)
    token = "job-v2-main-agent-" + secrets.token_urlsafe(36)
    token_path = output / "main_agent_token"
    descriptor = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, (token + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    spec_path = output / "bootstrap_spec.json"
    args = argparse.Namespace(
        bundle_manifest=bundle / "bundle_manifest.json",
        mysql_image_reference=MYSQL_REFERENCE,
        mysql_image_id=MYSQL_IMAGE_ID,
        client_image_reference=MYSQL_REFERENCE,
        client_image_id=MYSQL_IMAGE_ID,
        main_agent_token_file=token_path,
        attempt_token=attempt,
        valid_minutes=VALID_MINUTES,
        output=spec_path,
    )
    spec = renderer.render(args)
    renderer.atomic(spec_path, renderer.canonical(spec))
    renderer.atomic(spec_path.with_name(spec_path.name + ".sha256"),
                    (hashlib.sha256(renderer.canonical(spec)).hexdigest() + "  " + spec_path.name + "\n").encode("ascii"))
    validation = validator.validate(bundle, spec_path)
    plan_path = output / "command_plan.json"
    plan = {
        "schema_version": "mysql_job_v32_qualification_only_command_plan_v1",
        "status": "SEALED_PLAN_NOT_EXECUTED",
        "bundle_id": BUNDLE_ID,
        "bundle_manifest_sha256": BUNDLE_MANIFEST_SHA256,
        "attempt_token": attempt,
        "bootstrap_spec_sha256": sha256(spec_path),
        "validation": validation,
        "qualification_only": True,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    write_sealed(plan_path, plan)
    materials = {
        "schema_version": "mysql_job_v32_bootstrap_materials_manifest_v1",
        "status": "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED",
        "bundle_id": BUNDLE_ID,
        "bundle_manifest_sha256": BUNDLE_MANIFEST_SHA256,
        "attempt_token": attempt,
        "created_at_utc": spec["created_at_utc"],
        "expires_at_utc": spec["expires_at_utc"],
        "valid_minutes": VALID_MINUTES,
        "files": {
            "bootstrap_spec.json": sha256(spec_path),
            "command_plan.json": sha256(plan_path),
            "main_agent_token": sha256(token_path),
        },
        "main_agent_token_mode": "0600",
        "qualification_only": True,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    write_sealed(output / "materials_manifest.json", materials)
    return {"status": materials["status"], "attempt_token": attempt,
            "expires_at_utc": spec["expires_at_utc"], "materials_directory": str(output),
            "bootstrap_spec": str(spec_path), "command_plan": str(plan_path),
            "main_agent_token_file": str(token_path), "materials_manifest": str(output / "materials_manifest.json"),
            "accepted_round_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    print(json.dumps(create(args.bundle, args.output_root), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (MaterialError, OSError, ValueError, validator.ValidationError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)

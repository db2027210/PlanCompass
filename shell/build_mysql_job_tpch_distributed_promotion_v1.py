#!/usr/bin/env python3
"""Promote sealed distributed staging with fresh local evidence only.

No SSH, process, container, cgroup, or database action is implemented here.
The output is either a debug-qualification authority bundle or a fresh-R0-only
launch bundle.  Formal outer execution, formal10, resume, and accepted-round
accounting remain prohibited.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import build_mysql_job_tpch_distributed_successors_v1 as staged
from shell import verify_mysql_job_tpch_distributed_promotion_evidence_v1 as evidence


BUILDER = Path(__file__).resolve()
VERIFIER = Path(evidence.__file__).resolve()
STAGED = {
    "job": (
        ROOT / "artifacts/mysql_job_v31_distributed_staged/"
        "87550b383151d2de8517a4960d7447d9402bd1afab7c2fc48e8edd7007e601b6"
    ),
    "tpch": (
        ROOT / "artifacts/mysql_tpch_v37_distributed_staged/"
        "e29cafa825e8ed9134e1467a3eafffcaa8572fc5a6d9a9ba0dd676a091248d27"
    ),
}
STAGED_MANIFEST_SHA256 = {
    "job": "3af6ad949f1a28e167c9b4259992fbb3e21191d61fdcc2d8db326fd540986c23",
    "tpch": "5d7bb5fabe0c81ba1db49ccaa20f4bb72ac892808dd69306fd35ceb9471f96cc",
}
OUTPUT_ROOTS = {
    "job": ROOT / "artifacts/mysql_job_v32_distributed_promotion",
    "tpch": ROOT / "artifacts/mysql_tpch_v38_distributed_promotion",
}
SERVICE = "shell.mysql_algorithm_c_distributed_promotion_services_v1:build"
READINESS_VERIFIER = (
    "shell/verify_mysql_distributed_promotion_readiness_v1.py"
)
LAUNCHER = {
    "job": "launch_job_v32_distributed_promotion.py",
    "tpch": "launch_tpch_v38_distributed_promotion.py",
}


class PromotionError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, indent=2, ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    return staged.sha256(path)


def load(path: Path, label: str) -> dict[str, Any]:
    try:
        return staged.load_object(path.resolve(), label)
    except staged.BuildError as exc:
        raise PromotionError(str(exc)) from exc


def verify_staged(name: str) -> dict[str, Any]:
    bundle = STAGED[name]
    manifest = load(bundle / "bundle_manifest.json", f"{name} staged manifest")
    if (
        bundle.name != manifest.get("bundle_id")
        or sha256(bundle / "bundle_manifest.json")
        != STAGED_MANIFEST_SHA256[name]
        or manifest.get("status") != "STAGED_NOT_LAUNCH_AUTHORIZED"
        or manifest.get("launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise PromotionError(f"{name} staged identity/boundary differs")
    staged.verify_file_map(
        bundle, manifest.get("files") or {}, f"{name} staged bundle"
    )
    source = Path(str(manifest.get("source_directory") or ""))
    source_manifest = load(
        source / "source_manifest.json", f"{name} staged source"
    )
    if (
        source_manifest.get("bundle_id") != manifest.get("source_bundle_id")
        or sha256(source / "source_manifest.json")
        != manifest.get("source_manifest_sha256")
    ):
        raise PromotionError(f"{name} staged source identity differs")
    staged.verify_file_map(
        source, source_manifest.get("files") or {}, f"{name} staged source"
    )
    return manifest


def _source_files(
    name: str,
    staged_manifest: Mapping[str, Any],
    authority: Mapping[str, Any],
) -> tuple[dict[str, bytes], dict[str, Any], str]:
    source = Path(str(staged_manifest["source_directory"]))
    old = load(source / "source_manifest.json", f"{name} staged source")
    payloads = {
        str(relative): (source / str(relative)).read_bytes()
        for relative in old["files"]
    }
    payloads[
        "shell/mysql_algorithm_c_distributed_promotion_services_v1.py"
    ] = service_payload(name, authority)
    payloads[READINESS_VERIFIER] = readiness_verifier_payload()
    payloads[
        "shell/verify_mysql_job_tpch_distributed_promotion_evidence_v1.py"
    ] = VERIFIER.read_bytes()
    identity = {
        "schema_version":
            "mysql_job_tpch_distributed_promoted_source_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scenario": evidence.EXPECTED[name]["scenario"],
        "mode": authority["mode"],
        "attempt_token": authority["attempt_token"],
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "staged_source_bundle_id": old["bundle_id"],
        "staged_source_manifest_sha256":
            sha256(source / "source_manifest.json"),
        "promotion_evidence_sha256": sha_bytes(canonical(authority)),
        "builder_sha256": sha256(BUILDER),
        "verifier_sha256": sha256(VERIFIER),
        "files": {
            relative: sha_bytes(payload)
            for relative, payload in sorted(payloads.items())
        },
    }
    source_id = sha_bytes(canonical(identity))
    return payloads, {**identity, "bundle_id": source_id}, source_id


def service_payload(
    name: str, authority: Mapping[str, Any]
) -> bytes:
    lane = evidence.EXPECTED[name]
    expected = {
        "scenario": lane["scenario"],
        "mode": authority["mode"],
        "attempt_token": authority["attempt_token"],
        "authority_sha256": sha_bytes(canonical(authority)),
        "service_factory": SERVICE,
        "fresh_r0_launch_authorized":
            authority["fresh_r0_launch_authorized"],
        "host": lane["ssh_target"],
        "hostname": lane["host_id"],
        "project": lane["project"],
        "template": lane["template"],
        "device": lane["device"],
        "cpuset": lane["cpuset"],
        "mems": lane["mems"],
        "planner_port": lane["planner_port"],
        "replay_port": lane["replay_port"],
        **dict(lane["envelope"]),
    }
    return f'''"""Evidence-bound distributed promotion service."""
from __future__ import annotations
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from mysql_algorithm_b_production_services import ProductionServices

EXPECTED = {expected!r}

class PromotionAuthorityError(RuntimeError):
    pass

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def validate_authority(config: Mapping[str, Any]) -> None:
    production = config.get("production") or {{}}
    promotion = config.get("promotion_authority") or {{}}
    if config.get("formal_scope") != "mysql_only" or config.get("cross_engine_comparison_prohibited") is not True:
        raise PromotionAuthorityError("mysql_only scope differs")
    if production.get("service_factory") != EXPECTED["service_factory"]:
        raise PromotionAuthorityError("service factory differs")
    if config.get("scenario") != EXPECTED["scenario"] or promotion.get("mode") != EXPECTED["mode"] or promotion.get("attempt_token") != EXPECTED["attempt_token"]:
        raise PromotionAuthorityError("promotion identity differs")
    if promotion.get("authority_sha256") != EXPECTED["authority_sha256"]:
        raise PromotionAuthorityError("promotion evidence identity differs")
    if config.get("formal10_authorized") is not False or config.get("accepted_round_authorized") is not False or config.get("outer_execution_authorized") is not False:
        raise PromotionAuthorityError("formal10/accepted/outer boundary differs")
    if EXPECTED["mode"] == "fresh_r0":
        if production.get("launch_authorized") is not True or config.get("fresh_r0_only") is not True:
            raise PromotionAuthorityError("fresh-R0 launch boundary differs")
    else:
        if production.get("launch_authorized") is not False or config.get("debug_qualification_launch_authorized") is not True:
            raise PromotionAuthorityError("debug qualification boundary differs")

def build(config: Mapping[str, Any]) -> Mapping[str, Any]:
    validate_authority(config)
    if EXPECTED["mode"] != "fresh_r0":
        raise PromotionAuthorityError(
            "DEBUG_QUALIFICATION_ONLY: use the main-agent qualification "
            "runner; formal campaign construction is prohibited"
        )
    services = ProductionServices(config)
    # Do not trust module-global LANES or a profile-only replacement here.
    # Bind every distributed endpoint and resource field from this immutable
    # promotion authority before a Campaign can be constructed.
    services.lane = replace(
        services.lane,
        host=EXPECTED["host"],
        hostname=EXPECTED["hostname"],
        project=EXPECTED["project"],
        template=EXPECTED["template"],
        device=EXPECTED["device"],
        cpuset=EXPECTED["cpuset"],
        mems=EXPECTED["mems"],
        cpu_cores=EXPECTED["cpu_quota_cores"],
        memory_bytes=EXPECTED["memory_bytes"],
        pids=EXPECTED["pids_max"],
        read_bps=EXPECTED["io_read_bps"],
        write_bps=EXPECTED["io_write_bps"],
        remote_port=EXPECTED["planner_port"],
        buffer_pool_bytes=EXPECTED["buffer_pool_bytes"],
    )
    services.replay_lane = replace(
        services.lane, remote_port=EXPECTED["replay_port"]
    )
    bound = {{
        "host": services.lane.host,
        "hostname": services.lane.hostname,
        "project": services.lane.project,
        "template": services.lane.template,
        "device": services.lane.device,
        "cpuset": services.lane.cpuset,
        "mems": services.lane.mems,
        "cpu_quota_cores": services.lane.cpu_cores,
        "memory_bytes": services.lane.memory_bytes,
        "pids_max": services.lane.pids,
        "io_read_bps": services.lane.read_bps,
        "io_write_bps": services.lane.write_bps,
        "planner_port": services.lane.remote_port,
        "replay_port": services.replay_lane.remote_port,
        "buffer_pool_bytes": services.lane.buffer_pool_bytes,
    }}
    for key, actual in bound.items():
        if actual != EXPECTED[key]:
            raise PromotionAuthorityError(
                "distributed Campaign binding differs at " + key
            )
    return services.bundle()
'''.encode("utf-8")


def readiness_verifier_payload() -> bytes:
    return b'''"""Readiness verifier for evidence-bound promotion bundles."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping

@dataclass(frozen=True)
class Check:
    requirement: str
    item: str
    passed: bool
    reason: str

def verify_document(document: Mapping[str, Any]) -> list[Check]:
    common = (
        document.get("schema_version")
        == "mysql_distributed_promotion_readiness_v1"
        and document.get("formal_scope") == "mysql_only"
        and document.get("cross_engine_comparison_prohibited") is True
        and document.get("formal10_authorized") is False
        and document.get("accepted_round_authorized") is False
        and document.get("outer_execution_authorized") is False
        and document.get("historical_r0_or_readback_reusable") is False
    )
    launch = (
        document.get("status") == "READY"
        and document.get("launch_authorized") is True
        and document.get("fresh_r0_launch_authorized") is True
        and document.get("mode") == "fresh_r0"
    )
    evidence = (
        document.get("same_attempt_evidence_bound") is True
        and document.get("runtime_tree_hash_bound") is True
        and document.get("static_gate_bound") is True
        and document.get("dynamic_gate_bound") is True
        and document.get("ongoing_dynamic_monitor_required") is True
    )
    checks = [
        Check("AUTH", "fresh_r0_only", common and launch and evidence, "promotion authorization boundary"),
        Check("SCHEMA", "promotion_v1", common, "schema and mysql_only scope"),
    ]
    checks.extend(
        Check(f"R{index:02d}", "sealed_promotion_evidence", common and launch and evidence, "same-attempt sealed runtime/readback/static/dynamic authority")
        for index in range(1, 12)
    )
    return checks

def verify_ready(document: Mapping[str, Any]) -> None:
    failed = [check for check in verify_document(document) if not check.passed]
    if failed:
        raise RuntimeError("NOT_READY: " + "; ".join(check.reason for check in failed))
'''


def _runtime_config(name: str) -> dict[str, Any]:
    expected = evidence.EXPECTED[name]
    result = copy.deepcopy(staged.RUNTIME[name])
    result.update({
        "state": "FRESH_LIVE_READBACK_BOUND",
        "runtime_install_root": expected["python_root"],
        "interpreter_path": expected["python_interpreter"],
        "tree_sha256": evidence.PYTHON_TREE_SHA256,
        "tree_sha256_semantics":
            "authority_legacy_location_bound_inventory_digest",
        "tree_content_sha256": evidence.PYTHON_TREE_CONTENT_SHA256,
        "tree_content_identity_algorithm":
            "sha256_canonical_relative_tree_entries_v3",
        "authority_manifest_sha256": evidence.PYTHON_MANIFEST_SHA256,
        "exact_entry_set_verified": True,
        "directory_entry_set_verified": True,
        "file_sha256_size_mode_verified": True,
        "symlink_target_verified": True,
        "special_entries_rejected": True,
        "interpreter_sha256": evidence.PYTHON_EXECUTABLE_SHA256,
        "launch_authorized": True,
    })
    return result


def promoted_config(
    name: str,
    mode: str,
    staged_bundle: Path,
    authority: Mapping[str, Any],
    source_id: str,
    source_manifest_sha: str,
) -> dict[str, Any]:
    result = copy.deepcopy(load(staged_bundle / "config.json", "staged config"))
    scenario = evidence.EXPECTED[name]["scenario"]
    production = dict(result.get("production") or {})
    production.update({
        "service_factory": SERVICE,
        "launch_authorized": mode == "fresh_r0",
    })
    result.update({
        "schema_version": "mysql_distributed_promotion_config_v1",
        "production": production,
        "authorization_stage": mode,
        "debug_qualification_launch_authorized": True,
        "fresh_r0_only": mode == "fresh_r0",
        "fresh_r0_launch_authorized": mode == "fresh_r0",
        "outer_execution_authorized": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "historical_r0_or_readback_reusable": False,
        "runtime_authority": _runtime_config(name),
        "jdk_runtime_authority": {
            **staged.jdk_runtime_authority(name),
            "target_root": evidence.EXPECTED[name].get("jdk_root"),
            "launch_authorized": (
                mode == "fresh_r0"
                and evidence.EXPECTED[name]["jdk_required"]
            ),
            "fresh_live_readback_bound":
                evidence.EXPECTED[name]["jdk_required"],
        },
        "promotion_authority": {
            "mode": mode,
            "attempt_token": authority["attempt_token"],
            "authority_sha256": sha_bytes(canonical(authority)),
            "same_attempt_only": True,
            "expires_at_utc": authority["expires_at_utc"],
            "ongoing_dynamic_monitor_required": mode == "fresh_r0",
        },
        "source_bundle": {
            "source": "source",
            "source_bundle_id": source_id,
            "source_manifest_sha256": source_manifest_sha,
        },
    })
    result["profile"] = staged.PROFILE_REL[name]
    resource = dict(result["execution_resource_authority"])
    resource.update({
        "execution_authorized": mode == "fresh_r0",
        "profile_path": staged.PROFILE_REL[name],
        "profile_sha256": authority["profile_sha256"],
    })
    result["execution_resource_authority"] = resource
    storage = dict(result["execution_storage_authority"])
    storage.update({
        "execution_authorized": mode == "fresh_r0",
        "manifest_sha256": authority["live_readback_sha256"],
        "fresh_remote_manifest_readback_required": False,
    })
    result["execution_storage_authority"] = storage
    output = (
        Path(evidence.EXPECTED[name]["project"])
        / "formal_sgd_runs"
        / (
            f"{name}_{mode}_{authority['attempt_token']}"
        )
    )
    result["output"] = {
        **dict(result.get("output") or {}),
        "directory": str(output),
    }
    outer = dict(result.get("formal_outer_protocol") or {})
    outer.update({
        "max_outer_attempts": 0,
        "target_accepted_shrinks": 0,
        "accepted_shrink_count": 0,
        "rejected_count": 0,
        "formal_checkpoint_path": str(
            output / "formal_outer_checkpoint.json"
        ),
        "formal_real_r0_checkpoint_path": str(output / "formal_real_r0.json"),
    })
    result["formal_outer_protocol"] = outer
    result.pop("early_stop_source_bundle", None)
    return result


def launcher_payload(name: str, mode: str, readiness_name: str) -> bytes:
    runtime = evidence.EXPECTED[name]["python_interpreter"]
    runtime_root = evidence.EXPECTED[name]["python_root"]
    java_home = evidence.EXPECTED[name].get("jdk_root")
    control_lock = (
        Path(evidence.EXPECTED[name]["project"])
        / "host_control"
        / f"{name}_promotion_remote_ssh.lock"
    )
    return f'''#!/usr/bin/env python3
"""Evidence-bound {mode} launcher; formal10 is prohibited."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys

BUNDLE = Path(__file__).resolve().parent
SOURCE = BUNDLE / "source"
CONFIG = BUNDLE / "config.json"
READINESS = BUNDLE / {readiness_name!r}
AUTHORITY = BUNDLE / "promotion_authority.json"
EXPECTED_RUNTIME = {runtime!r}
EXPECTED_RUNTIME_ROOT = {runtime_root!r}
EXPECTED_JAVA_HOME = {java_home!r}
EXPECTED_CONTROL_LOCK = {str(control_lock)!r}
EXPECTED_SERVICE = {SERVICE!r}
MODE = {mode!r}
FORBIDDEN = {{
    "--config", "--readiness-manifest", "--service-factory",
    "--r0-authority-readiness-manifest", "--resume-directory",
    "--output-root", "--profile-json", "--host", "--port", "--runtime",
}}

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
if manifest.get("bundle_id") != BUNDLE.name:
    raise RuntimeError("promotion bundle identity differs")
for relative, digest in (manifest.get("files") or {{}}).items():
    path = BUNDLE / relative
    if not path.is_file() or path.is_symlink() or sha(path) != digest:
        raise RuntimeError("promotion closure differs: " + relative)
authority = json.loads(AUTHORITY.read_text())
if datetime.fromisoformat(authority["expires_at_utc"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
    raise RuntimeError("promotion evidence expired")
if any(arg in FORBIDDEN for arg in sys.argv[1:]):
    raise RuntimeError("authority parameter override is prohibited")
if MODE != "fresh_r0":
    print(json.dumps({{
        "status": "DEBUG_QUALIFICATION_AUTHORIZED",
        "formal_campaign_started": False,
        "main_agent_qualification_runner_required": True,
    }}, sort_keys=True))
    raise SystemExit(0)
if sys.executable != EXPECTED_RUNTIME or sha(Path(sys.executable)) != {evidence.PYTHON_EXECUTABLE_SHA256!r}:
    raise RuntimeError("Python runtime live identity differs")
runtime_helper_path = SOURCE / "shell/runtime_tree_content_identity_v3.py"
runtime_spec = importlib.util.spec_from_file_location(
    "_sealed_runtime_tree_content_identity_v3", runtime_helper_path
)
if runtime_spec is None or runtime_spec.loader is None:
    raise RuntimeError("cannot load sealed runtime tree verifier")
runtime_module = importlib.util.module_from_spec(runtime_spec)
runtime_spec.loader.exec_module(runtime_module)
python_manifest_path = SOURCE / {staged.PYTHON_PORTABLE_MANIFEST_REL!r}
python_manifest = json.loads(python_manifest_path.read_text())
python_readback = runtime_module.verify_tree_manifest(
    EXPECTED_RUNTIME_ROOT, python_manifest
)
if (
    python_readback.get("tree_content_sha256")
    != {evidence.PYTHON_TREE_CONTENT_SHA256!r}
    or python_readback.get("algorithm")
    != "sha256_canonical_relative_tree_entries_v3"
):
    raise RuntimeError("Python runtime exact tree readback differs")
if EXPECTED_JAVA_HOME is not None:
    jdk_manifest_path = SOURCE / {staged.JDK_PORTABLE_MANIFEST_REL!r}
    jdk_manifest = json.loads(jdk_manifest_path.read_text())
    jdk_readback = runtime_module.verify_tree_manifest(
        EXPECTED_JAVA_HOME, jdk_manifest
    )
    if (
        jdk_readback.get("tree_content_sha256")
        != {evidence.JDK_TREE_CONTENT_SHA256!r}
        or jdk_readback.get("algorithm")
        != "sha256_canonical_relative_tree_entries_v3"
    ):
        raise RuntimeError("JDK runtime exact tree readback differs")
    java_binary = Path(EXPECTED_JAVA_HOME) / "bin/java"
    if sha(java_binary) != {evidence.JAVA_EXECUTABLE_SHA256!r}:
        raise RuntimeError("JDK java executable differs")
    os.environ["JAVA_HOME"] = EXPECTED_JAVA_HOME
    os.environ["RA_JAVA_BINARY"] = str(java_binary)
    os.environ["PATH"] = str(Path(EXPECTED_JAVA_HOME) / "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["RA_CONTROL_PLANE_SSH_LOCK"] = EXPECTED_CONTROL_LOCK
sys.path[:] = [str(SOURCE)] + [p for p in sys.path if p not in ("", str(SOURCE))]
shell_package = type(sys)("shell")
shell_package.__path__ = [str(SOURCE / "shell")]
sys.modules["shell"] = shell_package
verifier = SOURCE / {READINESS_VERIFIER!r}
spec = importlib.util.spec_from_file_location("shell.verify_mysql_sgd_v4_readiness", verifier)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load promotion readiness verifier")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
sys.argv = [
    str(SOURCE / "shell/debug_mysql_gradient_round_v25.py"),
    *sys.argv[1:],
    "--config", str(CONFIG),
    "--readiness-manifest", str(READINESS),
    "--service-factory", EXPECTED_SERVICE,
]
runpy.run_path(str(SOURCE / "shell/debug_mysql_gradient_round_v25.py"), run_name="__main__")
'''.encode("utf-8")


def _copy_evidence_payloads(
    result: Mapping[str, Any],
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for label, pointer in result["evidence"].items():
        if pointer is None:
            continue
        path = Path(pointer["path"])
        document = load(path, f"{label} evidence")
        payloads[f"evidence/{label}.json"] = path.read_bytes()
        for relative in document["raw_artifacts"]:
            payloads[f"evidence/{label}_raw/{relative}"] = (
                path.parent / relative
            ).read_bytes()
    payloads[
        "evidence/runtime/python_manifest.json"
    ] = evidence.PYTHON_MANIFEST.read_bytes()
    payloads[
        "evidence/runtime/jdk_manifest.json"
    ] = evidence.JDK_MANIFEST.read_bytes()
    return payloads


def _full_staged_payloads(name: str) -> dict[str, bytes]:
    bundle = STAGED[name]
    manifest = load(bundle / "bundle_manifest.json", "staged manifest")
    payloads = {
        "staged/bundle_manifest.json":
            (bundle / "bundle_manifest.json").read_bytes(),
    }
    for relative in manifest["files"]:
        payloads[f"staged/{relative}"] = (bundle / relative).read_bytes()
    return payloads


def _readiness(
    name: str,
    mode: str,
    authority: Mapping[str, Any],
    source_id: str,
    config_sha: str,
) -> bytes:
    return canonical({
        "schema_version": "mysql_distributed_promotion_readiness_v1",
        "status": "READY" if mode == "fresh_r0"
        else "DEBUG_QUALIFICATION_AUTHORIZED",
        "promotion_status": (
            "FRESH_R0_LAUNCH_AUTHORIZED"
            if mode == "fresh_r0"
            else "DEBUG_QUALIFICATION_AUTHORIZED"
        ),
        "scenario": evidence.EXPECTED[name]["scenario"],
        "mode": mode,
        "attempt_token": authority["attempt_token"],
        "expires_at_utc": authority["expires_at_utc"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "debug_qualification_launch_authorized": True,
        "launch_authorized": mode == "fresh_r0",
        "fresh_r0_launch_authorized": mode == "fresh_r0",
        "outer_execution_authorized": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "historical_r0_or_readback_reusable": False,
        "source_bundle_id": source_id,
        "config_sha256": config_sha,
        "promotion_authority_sha256": sha_bytes(canonical(authority)),
        "same_attempt_evidence_bound": True,
        "runtime_tree_hash_bound": True,
        "static_gate_bound": True,
        "dynamic_gate_bound": mode == "fresh_r0",
        "ongoing_dynamic_monitor_required": mode == "fresh_r0",
    })


def _verify_publication(
    bundle: Path, expected_manifest: Mapping[str, Any]
) -> None:
    manifest = load(bundle / "bundle_manifest.json", "promotion manifest")
    if manifest != expected_manifest or bundle.name != manifest.get("bundle_id"):
        raise PromotionError("existing promotion identity differs")
    if sha256(bundle / "closure.json") != bundle.name:
        raise PromotionError("promotion content address differs")
    staged.verify_file_map(
        bundle, manifest.get("files") or {}, "promotion bundle"
    )


def build_promotion(
    *,
    scenario: str,
    mode: str,
    live_readback: Path,
    static_gate: Path,
    dynamic_gate: Path | None = None,
    execute: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    staged_manifest = verify_staged(scenario)
    verified = evidence.verify_promotion_evidence(
        scenario=scenario,
        mode=mode,
        live_readback_path=live_readback,
        static_gate_path=static_gate,
        dynamic_gate_path=dynamic_gate,
        now=now,
    )
    live_document = load(live_readback.resolve(), "live readback")
    authority = {
        **verified,
        "expires_at_utc": live_document["expires_at_utc"],
        "live_readback_sha256": sha256(live_readback.resolve()),
        "profile_sha256": staged_manifest[
            "preserved_envelope"
        ] and load(STAGED[scenario] / "config.json", "staged config")[
            "execution_resource_authority"
        ]["profile_sha256"],
        "qualification_data_plane_authority": "main_agent_only",
        "formal10_authorized": False,
        "accepted_round_authorized": False,
    }
    source_payloads, source_manifest, source_id = _source_files(
        scenario, staged_manifest, authority
    )
    source_manifest_payload = canonical(source_manifest)
    config = promoted_config(
        scenario,
        mode,
        STAGED[scenario],
        authority,
        source_id,
        sha_bytes(source_manifest_payload),
    )
    config_payload = canonical(config)
    readiness = _readiness(
        scenario, mode, authority, source_id, sha_bytes(config_payload)
    )
    readiness_name = f"readiness_manifest-{sha_bytes(readiness)}.json"
    payloads = {
        "config.json": config_payload,
        "promotion_authority.json": canonical(authority),
        readiness_name: readiness,
        LAUNCHER[scenario]:
            launcher_payload(scenario, mode, readiness_name),
        "source/source_manifest.json": source_manifest_payload,
        **{
            f"source/{relative}": payload
            for relative, payload in source_payloads.items()
        },
        **_copy_evidence_payloads(verified),
        **_full_staged_payloads(scenario),
    }
    identity = {
        "schema_version":
            "mysql_job_tpch_distributed_promotion_bundle_v1",
        "scenario": evidence.EXPECTED[scenario]["scenario"],
        "mode": mode,
        "attempt_token": verified["attempt_token"],
        "status": (
            "FRESH_R0_LAUNCH_AUTHORIZED_FORMAL10_PROHIBITED"
            if mode == "fresh_r0"
            else "DEBUG_QUALIFICATION_AUTHORIZED_FORMAL_CAMPAIGN_PROHIBITED"
        ),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "debug_qualification_launch_authorized": True,
        "fresh_r0_launch_authorized": mode == "fresh_r0",
        "outer_execution_authorized": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "staged_bundle_id": STAGED[scenario].name,
        "staged_bundle_manifest_sha256":
            STAGED_MANIFEST_SHA256[scenario],
        "source_bundle_id": source_id,
        "source_manifest_sha256": sha_bytes(source_manifest_payload),
        "promotion_authority_sha256":
            sha_bytes(canonical(authority)),
        "builder_sha256": sha256(BUILDER),
        "verifier_sha256": sha256(VERIFIER),
        "publication": "staged_then_atomic_rename",
        "idempotency": "content_addressed_existing_verified",
        "authority_parameter_override_prohibited": True,
        "files_without_closure": {
            relative: sha_bytes(payload)
            for relative, payload in sorted(payloads.items())
        },
    }
    bundle_id = sha_bytes(canonical(identity))
    payloads["closure.json"] = canonical(identity)
    manifest = {
        **identity,
        "bundle_id": bundle_id,
        "files": {
            relative: sha_bytes(payload)
            for relative, payload in sorted(payloads.items())
        },
    }
    final = OUTPUT_ROOTS[scenario] / bundle_id
    if final.exists():
        _verify_publication(final, manifest)
    elif execute:
        OUTPUT_ROOTS[scenario].mkdir(parents=True, exist_ok=True)
        stage_path = (
            OUTPUT_ROOTS[scenario]
            / f".{bundle_id}.tmp.{os.getpid()}"
        )
        stage_path.mkdir()
        try:
            for relative, payload in payloads.items():
                path = stage_path / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            (stage_path / "bundle_manifest.json").write_bytes(
                canonical(manifest)
            )
            staged.readonly(stage_path)
            stage_path.rename(final)
        except BaseException:
            if stage_path.exists():
                staged.remove_private_stage(stage_path)
            raise
        _verify_publication(final, manifest)
    return {
        "schema_version":
            "mysql_job_tpch_distributed_promotion_build_result_v1",
        "status": (
            "PUBLISHED" if execute else "READY_TO_PUBLISH"
        ),
        "scenario": evidence.EXPECTED[scenario]["scenario"],
        "mode": mode,
        "attempt_token": verified["attempt_token"],
        "bundle_directory": str(final),
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": (
            sha256(final / "bundle_manifest.json")
            if final.is_dir()
            else sha_bytes(canonical(manifest))
        ),
        "remote_commands_executed": False,
        "remote_data_plane_modified": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("job", "tpch"), required=True)
    parser.add_argument(
        "--mode", choices=("debug_qualification", "fresh_r0"), required=True
    )
    parser.add_argument("--live-readback", type=Path, required=True)
    parser.add_argument("--static-gate", type=Path, required=True)
    parser.add_argument("--dynamic-gate", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not getattr(staged, "RUNTIME_V3_AUTHORITY_INTEGRATED", False):
        print(json.dumps({
            "status": "BLOCKED",
            "remote_commands_executed": False,
            "formal10_authorized": False,
            "accepted_round_authorized": False,
            "reason": (
                "directory-aware portable runtime v3 is not integrated; "
                "new immutable successors are required"
            ),
        }, indent=2, sort_keys=True))
        return 2
    try:
        result = build_promotion(
            scenario=args.scenario,
            mode=args.mode,
            live_readback=args.live_readback,
            static_gate=args.static_gate,
            dynamic_gate=args.dynamic_gate,
            execute=args.execute,
        )
    except (
        evidence.EvidenceError, PromotionError, staged.BuildError,
        OSError, ValueError,
    ) as exc:
        print(json.dumps({
            "status": "BLOCKED",
            "remote_commands_executed": False,
            "formal10_authorized": False,
            "accepted_round_authorized": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

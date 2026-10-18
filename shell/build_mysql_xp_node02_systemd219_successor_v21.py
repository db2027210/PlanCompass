#!/usr/bin/env python3
"""Build immutable XP v21 with audited systemd-219 empty-cgroup success."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import build_mysql_xp_node02_materials_successor_v20 as base


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f"
PARENT_MANIFEST_SHA256 = "3df7740447d613a653ef500100a54edf755ab05b8728336460ac96b5105ee17f"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v20" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v21"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
AUTHORITY = "qualification/mysql_xp_qualification_authority_v1.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
MATERIALIZER = "launch/mysql_xp_node02_formal10_materializer_v1.py"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
COMPAT_TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
READINESS_TEST = "tests/test_mysql_xp_schema_readiness_v19.py"
V20_TEST = "tests/test_mysql_xp_materials_successor_v20.py"
REGRESSION_TEST = "tests/test_mysql_xp_systemd219_empty_cgroup_v21.py"
BUILDER = "build_mysql_xp_node02_systemd219_successor_v21.py"
REMEDIATION = "xp_node02_systemd219_empty_cgroup_remediation_v1.json"


class BuildError(RuntimeError):
    pass


def replace(payload: bytes, old: str, new: str, count: int, label: str) -> bytes:
    try:
        return base.replace_exact(payload, old, new, count, label)
    except base.BuildError as exc:
        raise BuildError(str(exc)) from exc


def patch_schema_list(payload: bytes, label: str) -> bytes:
    return replace(
        payload,
        '            "mysql_xp_node02_successor_bundle_v20",\n',
        '            "mysql_xp_node02_successor_bundle_v20",\n'
        '            "mysql_xp_node02_successor_bundle_v21",\n',
        1, label,
    )


def patch_materials(payload: bytes) -> bytes:
    payload = replace(
        payload,
        '"""Create local-only, short-lived bootstrap materials for XP v19/v20.',
        '"""Create local-only, short-lived bootstrap materials for XP v19-v21.',
        1, "materials documentation",
    )
    payload = replace(
        payload,
        '''V19_PREDECESSOR_ID = (
    "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''',
        '''V19_PREDECESSOR_ID = (
    "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262"
)
V20_BUNDLE_ID = (
    "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f"
)
V20_MANIFEST_SHA256 = (
    "3df7740447d613a653ef500100a54edf755ab05b8728336460ac96b5105ee17f"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''',
        1, "v20 exact identity constants",
    )
    payload = replace(
        payload,
        '''    "mysql_xp_node02_successor_bundle_v20": V19_BUNDLE_ID,
}
''',
        '''    "mysql_xp_node02_successor_bundle_v20": V19_BUNDLE_ID,
    "mysql_xp_node02_successor_bundle_v21": V20_BUNDLE_ID,
}
''',
        1, "v21 schema policy",
    )
    payload = replace(
        payload,
        '''    if schema == "mysql_xp_node02_successor_bundle_v19" and (
        bundle.name != V19_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V19_MANIFEST_SHA256
    ):
        raise MaterialError("v19 sealed content identity differs")
''',
        '''    if schema == "mysql_xp_node02_successor_bundle_v19" and (
        bundle.name != V19_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V19_MANIFEST_SHA256
    ):
        raise MaterialError("v19 sealed content identity differs")
    if schema == "mysql_xp_node02_successor_bundle_v20" and (
        bundle.name != V20_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V20_MANIFEST_SHA256
    ):
        raise MaterialError("v20 sealed content identity differs")
''',
        1, "v20 exact identity check",
    )
    old = payload.decode("utf-8")
    start = old.index("def controller_poll_spec(unit):\n")
    end = old.index("def write_sealed(path, value, mode=0o444):\n", start)
    audit_code = '''def _exec_argv_sha256(argv):
    return hashlib.sha256(canonical(list(argv))).hexdigest()


def controller_poll_spec(unit, expected_exec_start_argv=None):
    if not unit.startswith("ra-mysql-xp-v12-"):
        raise MaterialError("controller poll unit differs")
    expected = list(expected_exec_start_argv or [])
    return {
        "argv": [
            "systemctl", "show", unit,
            "--property=Id",
            "--property=Transient",
            "--property=RemainAfterExit",
            "--property=ExecStart",
            "--property=ActiveState",
            "--property=SubState",
            "--property=ExecMainStatus",
            "--property=Result",
            "--property=MainPID",
            "--property=ControlGroup",
        ],
        "poll_interval_seconds": 1,
        "deadline_seconds": 1800,
        "expected_exec_start_argv": expected,
        "expected_exec_start_argv_sha256": _exec_argv_sha256(expected),
        "running_state": {
            "Id": unit,
            "Transient": "yes",
            "RemainAfterExit": "yes",
            "ActiveState": "activating",
            "SubState": "start",
            "MainPID": "positive",
            "ControlGroup": "/system.slice/" + unit,
        },
        "success_state": {
            "Id": unit,
            "Transient": "yes",
            "RemainAfterExit": "yes",
            "ActiveState": "active",
            "SubState": "exited",
            "ExecMainStatus": "0",
            "Result": "success",
            "MainPID": "0",
            "ControlGroup": "/system.slice/" + unit,
        },
        "systemd219_empty_control_group_terminal_success": {
            "ControlGroup": "",
            "all_success_state_fields_still_required": True,
            "exact_normalized_exec_start_argv_required": True,
            "prior_running_exact_cgroup_pid_and_proc_identity_or_wrapper_seal_required": True,
            "sealed_unit_journal_sha256_required": True,
            "sealed_stage_output_sha256_required": True,
            "journal_and_output_content_must_rehash_to_declared_sha256": True,
            "missing_or_tampered_evidence_policy": "FAIL_CLOSED_ABANDON_ATTEMPT",
        },
        "all_other_states": "terminal_failure_or_fail_closed",
        "timeout_policy": "fail_closed_and_abandon_attempt",
        "reset_failed_prohibited": True,
        "stop_prohibited": True,
        "collect_prohibited": True,
        "unit_retained_for_audit": True,
    }


def parse_systemctl_show(stdout):
    result = {}
    for line in str(stdout).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def _unit_identity(row, unit, normalized_exec_start_argv, expected_argv):
    return (
        row.get("Id") == unit
        and row.get("Transient") == "yes"
        and row.get("RemainAfterExit") == "yes"
        and list(normalized_exec_start_argv or []) == list(expected_argv or [])
    )


def _running_identity(evidence, unit, expected_argv):
    if not isinstance(evidence, dict):
        return False
    row = parse_systemctl_show(evidence.get("systemctl_show", ""))
    cgroup = "/system.slice/" + unit
    try:
        pid = int(row.get("MainPID") or 0)
        starttime = int(evidence.get("pid_starttime_ticks") or 0)
    except (TypeError, ValueError):
        return False
    return (
        _unit_identity(row, unit, evidence.get("normalized_exec_start_argv"), expected_argv)
        and row.get("ActiveState") == "activating"
        and row.get("SubState") == "start"
        and pid > 1
        and row.get("ControlGroup") == cgroup
        and evidence.get("process_cgroup") == cgroup
        and starttime > 0
    )


def _wrapper_identity(evidence, unit, expected_argv):
    if not isinstance(evidence, dict):
        return False
    unsigned = dict(evidence)
    identity_sha = unsigned.pop("identity_sha256", None)
    try:
        pid = int(unsigned.get("pid") or 0)
        starttime = int(unsigned.get("pid_starttime_ticks") or 0)
    except (TypeError, ValueError):
        return False
    return (
        SHA256_RE.fullmatch(str(identity_sha or "")) is not None
        and hashlib.sha256(canonical(unsigned)).hexdigest() == identity_sha
        and unsigned.get("unit") == unit
        and unsigned.get("cgroup") == "/system.slice/" + unit
        and pid > 1
        and starttime > 0
        and unsigned.get("exec_start_argv_sha256") == _exec_argv_sha256(expected_argv)
    )


def _sealed_hash_evidence(evidence, unit):
    if not isinstance(evidence, dict) or not isinstance(evidence.get("content"), str):
        return False
    digest = str(evidence.get("sha256") or "")
    return (
        evidence.get("unit") == unit
        and evidence.get("sealed") is True
        and SHA256_RE.fullmatch(digest) is not None
        and hashlib.sha256(evidence["content"].encode("utf-8")).hexdigest() == digest
    )


def classify_systemctl_audit(audit, unit, expected_exec_start_argv):
    if not isinstance(audit, dict):
        return "failure"
    row = parse_systemctl_show(audit.get("systemctl_show", ""))
    expected = list(expected_exec_start_argv or [])
    cgroup = "/system.slice/" + unit
    identity = _unit_identity(
        row, unit, audit.get("normalized_exec_start_argv"), expected
    )
    try:
        pid = int(row.get("MainPID") or 0)
    except (TypeError, ValueError):
        return "failure"
    if (
        identity
        and row.get("ActiveState") == "activating"
        and row.get("SubState") == "start"
        and pid > 1
        and row.get("ControlGroup") == cgroup
    ):
        return "running"
    terminal = (
        identity
        and row.get("ActiveState") == "active"
        and row.get("SubState") == "exited"
        and row.get("ExecMainStatus") == "0"
        and row.get("Result") == "success"
        and row.get("MainPID") == "0"
    )
    if terminal and row.get("ControlGroup") == cgroup:
        return "success"
    if terminal and row.get("ControlGroup") == "":
        runtime_identity = (
            _running_identity(audit.get("prior_running"), unit, expected)
            or _wrapper_identity(audit.get("wrapper_running_identity"), unit, expected)
        )
        if (
            runtime_identity
            and _sealed_hash_evidence(audit.get("journal_evidence"), unit)
            and _sealed_hash_evidence(audit.get("output_evidence"), unit)
        ):
            return "success"
    return "failure"


def classify_systemctl_show(stdout, unit):
    """Legacy strict classifier; empty terminal cgroups require full audit."""
    row = parse_systemctl_show(stdout)
    cgroup = "/system.slice/" + unit
    if (
        row.get("ActiveState") == "activating"
        and row.get("SubState") == "start"
        and int(row.get("MainPID") or 0) > 1
        and row.get("ControlGroup") == cgroup
    ):
        return "running"
    if (
        row.get("ActiveState") == "active"
        and row.get("SubState") == "exited"
        and row.get("ExecMainStatus") == "0"
        and row.get("Result") == "success"
        and row.get("MainPID") == "0"
        and row.get("ControlGroup") == cgroup
    ):
        return "success"
    return "failure"


'''
    payload = (old[:start] + audit_code + old[end:]).encode("utf-8")
    payload = replace(
        payload,
        '''    def controller_poll(action):
        return controller_poll_spec(executor_units[action])
''',
        '''    def controller_poll(action, inner):
        return controller_poll_spec(executor_units[action], inner)
''',
        1, "controller expected argv binding",
    )
    return replace(
        payload,
        '''            "physical_copy": controller_poll("copy"),
            "plan_only": controller_poll("plan"),
            "bootstrap": controller_poll("bootstrap"),
            "adopt_readback": controller_poll("adopt"),
''',
        '''            "physical_copy": controller_poll("copy", physical_copy),
            "plan_only": controller_poll("plan", plan_only),
            "bootstrap": controller_poll("bootstrap", bootstrap),
            "adopt_readback": controller_poll("adopt", adopt_readback),
''',
        1, "four controller argv bindings",
    )


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = base.load(manifest_path, "parent manifest")
    unsigned = dict(manifest)
    actual_id = unsigned.pop("bundle_id", None)
    if (
        actual_id != PARENT_ID
        or base.sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or base.sha_bytes(base.canonical(unsigned)) != PARENT_ID
        or base.relative_files(PARENT) != manifest.get("files")
        or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v20"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, scope, or authority differs")
    return manifest


def build() -> Path:
    parent = verify_parent()
    content = {r: (PARENT / r).read_bytes() for r in sorted(parent["files"])}
    content[SERVICE] = replace(
        content[SERVICE],
        '        != "mysql_xp_node02_successor_bundle_v20"',
        '        != "mysql_xp_node02_successor_bundle_v21"',
        1, "service schema",
    )
    content[AUTHORITY] = patch_schema_list(content[AUTHORITY], "authority schemas")
    content[MATERIALIZER] = patch_schema_list(content[MATERIALIZER], "materializer schemas")
    content[COLLECTOR] = replace(
        content[COLLECTOR],
        '        "mysql_xp_node02_successor_bundle_v20",\n',
        '        "mysql_xp_node02_successor_bundle_v20",\n'
        '        "mysql_xp_node02_successor_bundle_v21",\n',
        1, "collector schemas",
    )
    content[MATERIALS] = patch_materials(content[MATERIALS])
    content[COMPAT_TEST] = replace(
        content[COMPAT_TEST],
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v20"',
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v21"',
        1, "compat test schema",
    )
    content[READINESS_TEST] = replace(
        content[READINESS_TEST],
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v20")]',
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v21")]',
        1, "readiness test schema",
    )
    content[V20_TEST] = replace(
        content[V20_TEST],
        'assert v20_manifest["schema_version"].endswith("_v20")',
        'assert v20_manifest["schema_version"].endswith("_v21")',
        1, "v20 test current schema",
    )
    content[V20_TEST] = replace(
        content[V20_TEST],
        'assert v20_manifest["predecessor_bundle_id"] == V19_ID',
        'assert v20_manifest["predecessor_bundle_id"] == "' + PARENT_ID + '"',
        1, "v20 test predecessor",
    )
    content[REGRESSION_TEST] = (ROOT / REGRESSION_TEST).read_bytes()
    content[BUILDER] = HERE.read_bytes()
    content[REMEDIATION] = base.canonical({
        "schema_version": "mysql_xp_node02_systemd219_empty_cgroup_remediation_v1",
        "status": "SEALED_OFFLINE_SUCCESSOR_NOT_LAUNCHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "parent_bundle_id": PARENT_ID,
        "invalidated_attempt_id": "xp-formal-qual-20260803T020736Z-461a0d8f869ed6c73246728f",
        "invalidated_attempt_credit": 0,
        "running_exact_control_group_and_pid_required": True,
        "terminal_empty_control_group_requires_exact_unit_and_exec_identity": True,
        "terminal_empty_control_group_requires_prior_running_or_wrapper_seal": True,
        "terminal_empty_control_group_requires_journal_and_output_hashes": True,
        "missing_or_tampered_evidence_policy": "FAIL_CLOSED_ABANDON_ATTEMPT",
        "database_cpu_quota_cores": 5.3,
        "database_io_read_bps": 29360128,
        "database_io_write_bps": 524288,
        "required_nonempty_subsets": 127,
        "fixed_real_replay_top_k": 5,
        "bootstrap_readiness_timeout_seconds": 600,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    })

    files = {r: base.sha_bytes(p) for r, p in sorted(content.items())}
    identity = {k: v for k, v in parent.items() if k not in ("bundle_id", "files")}
    identity.update({
        "schema_version": "mysql_xp_node02_successor_bundle_v21",
        "status": "SEALED_SYSTEMD219_EMPTY_CGROUP_AUDITED_SUCCESSOR_NOT_LAUNCHED",
        "builder_revision": "v21_systemd219_empty_cgroup_audited_terminal_success",
        "builder_sha256": base.sha_file(HERE),
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "supersedes_bundle_id": PARENT_ID,
        "service_sha256": files[SERVICE],
        "collector_sha256": files[COLLECTOR],
        "materials_generator_sha256": files[MATERIALS],
        "fresh_r0_required": True,
        "historical_accepted_round_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = base.sha_bytes(base.canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".xp-v21.", dir=OUTPUT_ROOT))
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            raw = base.canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                base.sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            base.freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if base.relative_files(destination) != files:
        raise BuildError("published successor closure differs")
    unsigned = dict(base.load(destination / "bundle_manifest.json", "manifest"))
    if unsigned.pop("bundle_id", None) != destination.name or base.sha_bytes(base.canonical(unsigned)) != destination.name:
        raise BuildError("published content identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_XP_V21_SYSTEMD219_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "verified_files": len(files),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, base.BuildError, OSError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))

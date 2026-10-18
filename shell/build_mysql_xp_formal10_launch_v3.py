#!/usr/bin/env python3
"""Build the transactional, content-addressed XP executable launch closure."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(__file__).resolve()
BASE = (
    ROOT / "artifacts/mysql_xp_distributed_successor_v1"
    / "ea1c826ffa33b409bb94df04eec0d45c70702b80c7c48ff44ac0ba557e20f1b9"
)
SUPERSEDED = (
    ROOT / "artifacts/mysql_xp_formal10_successor_v2"
    / "8b9bc8a3c82ebf3d6ee9497b0fcb91a553f32bbacc4cb1ffede5820b9a112bf2"
)
INVALIDATED_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "7d1fa34305637124dcbb4d1f97a91d77ee6f9810a5aa4ae229a8433de2b4df0f"
)
INVALIDATED_TOKENLESS_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "fd5fd07dcf42daf590fb08ea6f88394531eac982ead2052ec348e27f7f0ec2df"
)
INVALIDATED_UNEXPIRING_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "fe1bcc7a8624e3fb1f12092c50e37216d730b2f2dfe55360c4cccfb631db0fb1"
)
INVALIDATED_UNBOUND_CONFIG_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "ad6c994c2ee4101d67f893558ef5be87712c5fbd40c417670631d84a95b6b08c"
)
INVALIDATED_PRECREATE_GATE_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "7f41936b910a6b87e126ec11507c8e4e47c7e95b2259345fe95b3807400829c6"
)
INVALIDATED_PORT_GATE_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "4130d5dcf5b08732f352833dc7ea7bb4b599f4e008477b82f44a76ff64d1b14f"
)
INVALIDATED_RUNTIME_SOCKET_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "637cf3a8d9e7fe85e979b846304a4cdb823f7566bcf6b17760d8c4b4c80bf503"
)
INVALIDATED_LEGACY_FINDMNT_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "96d938614f88e37445d56a3c0d4eeef960e6d0990f7df56c85aca3c11480fceb"
)
INVALIDATED_LEGACY_DOCKER_MOUNT_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "3599cd89ad2950039bb2c685d9f3edf540e23ceae715fb4ccac42f41bb772111"
)
INVALIDATED_PARTITION_THROTTLE_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "79815c2fe8c1fc8f31177e7be6954f73bbd2a6ac4a95488ad6d36aa6bb281f1a"
)
INVALIDATED_UNWRITABLE_DATADIR_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "d55f477550f0616f80d7701eeb7c55e9c7e30590e3e1cdea34f05f2924d393ad"
)
INVALIDATED_BYTECODE_POLLUTED_LAUNCH = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v3"
    / "0a97c902540917339fac5cb365a9d3c390d6f40a0ab3fe0eda2a18e72aa110b9"
)
INVALIDATED_QUALIFICATION_CONFIG_BUNDLE_ID = (
    "b67bf54fe87f540591170e01c757dc24e622ea0ae80a6970b305eb33c64dfaaa"
)
INVALIDATED_QUALIFICATION_CONFIG_MANIFEST_SHA256 = (
    "577f86de52e7216a4c08c0ae8a581a115c861bd847718ba4e70d50b767327fa8"
)
INVALIDATED_MATERIALIZER_COLLECTOR_BUNDLE_ID = (
    "e550e412b6e8bd226c2299252ae7dc61c33d8fcd43506dd5741e8e72b251d4a6"
)
INVALIDATED_MATERIALIZER_COLLECTOR_MANIFEST_SHA256 = (
    "70fdd4490240cc9745680bbab34010d9abb155a2fe4cf912c85976156a938b54"
)
INVALIDATED_PAIRED_AUTHORITY_BUNDLE_ID = (
    "a8f508d566b934e226c1a896ab85b917e9c0058162939dd6fb68b942f80b42b5"
)
INVALIDATED_PAIRED_AUTHORITY_MANIFEST_SHA256 = (
    "f430c59d7a1dce5896b37ce863026b198a868665baacf976146270f8fe00d1e2"
)
INVALIDATED_GATE_BINDING_BUNDLE_ID = (
    "c063606102e1087d0f12c69edb5c7b90055642ee0950a83fa7eb4e9d55ca123f"
)
INVALIDATED_GATE_BINDING_MANIFEST_SHA256 = (
    "134ca32c13f2acf5197e5da9bfc1d9676ad6387ff0e74a9151987da580445098"
)
INVALIDATED_MISSING_PRODUCER_BUNDLE_ID = (
    "243f4c6232773952ee3bcf18a9547193b04a20c1775281f0b8c0c664954c968c"
)
INVALIDATED_MISSING_PRODUCER_MANIFEST_SHA256 = (
    "8cc45de6f8c38356776225ae3b5739307feebee501457e19ae84d3a7237a0660"
)
INVALIDATED_RAW_EXECUTOR_MISSING_BUNDLE_ID = (
    "0d100dce6db03656ee1484fe85ff00673c6671c8bc3c1838763fb1e8f5f24581"
)
INVALIDATED_RAW_EXECUTOR_MISSING_MANIFEST_SHA256 = (
    "d2e4ce0dcb779184089d78e9c5a8b1597b0008981fa3192da5a4dea813a5b672"
)
INVALIDATED_COLLECTOR_SUPERSEDED_BUNDLE_ID = (
    "dec0abce9b7dcc5cef90facf5fb8df7e0b3527e757052879389716be298aa884"
)
INVALIDATED_COLLECTOR_SUPERSEDED_MANIFEST_SHA256 = (
    "2cca6569adc8a1c039aca175cb6833be3c966d58f9c11a5605e3f701a2bd5b0d"
)
INVALIDATED_SHOW_STATUS_PARSER_BUNDLE_ID = (
    "afaba42a6a59b463c0f006feac88dc7b5adeddcbe6f068e47c7e3bfba5d30807"
)
INVALIDATED_SHOW_STATUS_PARSER_MANIFEST_SHA256 = (
    "0963da1ddee3b90a314a69b49cd7bdeb5d63785c5e84a1802c3c514e8f88b606"
)
INVALIDATED_UNBOUND_CAPTURE_TRANSITION_BUNDLE_ID = (
    "0207f72f93752194d72ed0d286f3c33f559c195a0411efb681db2fefa0797454"
)
INVALIDATED_UNBOUND_CAPTURE_TRANSITION_MANIFEST_SHA256 = (
    "f43fcfe68b878f44e9a8b7edc960ca8c4fe9c15a35be11721033bb91fa2900c7"
)
A0CC_DYNAMIC_ADOPTION_IDENTITY_BUNDLE_ID = (
    "a0cc7b9fe8ac898ddb612b0c79be493e44aa333da5e14dfb14b6adb9bfbfac63"
)
A0CC_DYNAMIC_ADOPTION_IDENTITY_MANIFEST_SHA256 = (
    "f483fcd54d475d0be27c16d55ca72fdc94140f68599ae93ae2a6cd3056cab92a"
)
CDD36_MISSING_BOOTSTRAP_ROOT_GATE_BUNDLE_ID = (
    "cdd36ff6cafd4a0b67b535d3ff68c63c685de9ee1ac1daa773b4bd9294136f89"
)
CDD36_MISSING_BOOTSTRAP_ROOT_GATE_MANIFEST_SHA256 = (
    "c002be490dff971688be51b962455079c5b1801f450ea02797eedab9ea94c571"
)
BED961_CLIENT_ANONYMOUS_VOLUME_BUNDLE_ID = (
    "bed961fe29c89e0fdeff13ea9b377b19e9cebf7b1bc6e0438b95af25a0e69873"
)
BED961_CLIENT_ANONYMOUS_VOLUME_MANIFEST_SHA256 = (
    "401d25eaa9a3d0ca313f31093743279acec7286e63c1cd59e76eed39bdc8d5d5"
)
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_formal10_launch_v3"
# XP successor changes must not absorb a concurrently edited shared collector.
# The ee9c closure is the last verified XP collector freeze point and is copied
# byte-for-byte until an XP-specific collector successor is explicitly made.
VERIFIED_EE9C = (
    OUTPUT_ROOT
    / "ee9c543838a4c57ced33bb69c4e861b2db5ddeb0e2e3bb5e6a06f903e94f0628"
)
SOURCES = {
    "launch/mysql_xp_node02_adopted_service_v1.py":
        ROOT / "shell/mysql_xp_node02_adopted_service_v1.py",
    "launch/mysql_xp_adoption_reconciliation_v1.py":
        ROOT / "shell/mysql_xp_adoption_reconciliation_v1.py",
    "client_masks/var_lib_mysql/.sealed-read-only-mask":
        ROOT
        / "config/formal/mysql_xp_node02_v3/"
        "client_var_lib_mysql_mask.marker",
    "launch/mysql_xp_capture_authority_v1.py":
        ROOT / "shell/mysql_xp_capture_authority_v1.py",
    "launch/mysql_xp_capture_transition_v1.py":
        ROOT / "shell/mysql_xp_capture_transition_v1.py",
    "launch/mysql_xp_node02_formal10_materializer_v1.py":
        ROOT / "shell/mysql_xp_node02_formal10_materializer_v1.py",
    "launch/mysql_xp_formal_evidence_producer_v1.py":
        ROOT / "shell/mysql_xp_formal_evidence_producer_v1.py",
    "launch/mysql_xp_raw_evidence_executor_v1.py":
        ROOT / "shell/mysql_xp_raw_evidence_executor_v1.py",
    "qualification/mysql_xp_qualification_authority_v1.py":
        ROOT / "shell/mysql_xp_qualification_authority_v1.py",
    (
        "qualification/native_latency_producer/classes/"
        "com/hybench/HyBenchFormalMain.class"
    ): (
        BASE
        / "qualification/native_latency_producer/classes/"
        "com/hybench/HyBenchFormalMain.class"
    ),
    "runtime/mysql_xp_node02_jdk_publish_v1.py":
        ROOT / "shell/mysql_xp_node02_jdk_publish_v1.py",
    "runtime/runtime_tree_content_identity_v3.py":
        ROOT / "shell/runtime_tree_content_identity_v3.py",
    "qualification/mysql_distributed_workload_metric_adapter_v1.py":
        ROOT / "shell/mysql_distributed_workload_metric_adapter_v1.py",
    # The collector imports this sibling at runtime.  Keeping only runtime/
    # copy made b679 non-executable after content-addressed publication.
    "qualification/runtime_tree_content_identity_v3.py":
        ROOT / "shell/runtime_tree_content_identity_v3.py",
}
QUALIFICATION_CONFIG_SOURCES = {
    "qualification/config/hybench_xp_qualification.properties":
        ROOT
        / "config/formal/mysql_xp_node02_v3/"
        "hybench_xp_qualification.properties",
    "qualification/config/mysql-client.cnf":
        ROOT / "config/formal/mysql_xp_node02_v3/mysql-client.cnf",
    "qualification/config/stmt_mysql_formal_case_normalized.toml":
        BASE / "predecessor/config/stmt_mysql_formal_case_normalized.toml",
}
BASE_COLLECTOR_SHA256 = (
    "2d27624a0a3a9c4efd83a7afcacbc223d368dad08c5ab70b06af235263a9bdb6"
)
XP_COLLECTOR_RELATIVE = (
    "qualification/mysql_distributed_live_readback_qualification_v1.py"
)
XP_COLLECTOR_BASE = VERIFIED_EE9C / XP_COLLECTOR_RELATIVE
INVALIDATED_PARENT_THROTTLE_BUNDLE_ID = (
    "9bfdd22abba1330fbb3938609f21102bccad68293b1d987b1dd9f7514a652568"
)
INVALIDATED_PARENT_THROTTLE_MANIFEST_SHA256 = (
    "31bbcbdee025996ca26f2feb8fc46acb24b52a2b0f34ea97f406195b31d94e10"
)
SUPERSEDED_ID = (
    "8b9bc8a3c82ebf3d6ee9497b0fcb91a553f32bbacc4cb1ffede5820b9a112bf2"
)
INVALID_DEVELOPMENT_BUNDLE_IDS = [
    # The collector import failed before argument parsing; never launch b679.
    "b6795da82aed6d5e3fa4c48a7639f1e813f7081b166d9fba9a30b854a49843c9",
    "eeefadb1651ff3e21675f6da328b59aa2f4445d4e3688fc4ed6b29a449570fc4",
    "25409e76dda1c29f5093450804450717ac7a0c05864bf0b2ec5e92d9df23121b",
    "63ab2c8b73cb5b0411a1eff955aeb1f8d38ae447489e8a24a8f298b25dc69118",
    "7d1fa34305637124dcbb4d1f97a91d77ee6f9810a5aa4ae229a8433de2b4df0f",
    "fd5fd07dcf42daf590fb08ea6f88394531eac982ead2052ec348e27f7f0ec2df",
    "fe1bcc7a8624e3fb1f12092c50e37216d730b2f2dfe55360c4cccfb631db0fb1",
    "ad6c994c2ee4101d67f893558ef5be87712c5fbd40c417670631d84a95b6b08c",
    "7f41936b910a6b87e126ec11507c8e4e47c7e95b2259345fe95b3807400829c6",
    "4130d5dcf5b08732f352833dc7ea7bb4b599f4e008477b82f44a76ff64d1b14f",
    "637cf3a8d9e7fe85e979b846304a4cdb823f7566bcf6b17760d8c4b4c80bf503",
    "96d938614f88e37445d56a3c0d4eeef960e6d0990f7df56c85aca3c11480fceb",
    "3599cd89ad2950039bb2c685d9f3edf540e23ceae715fb4ccac42f41bb772111",
    "79815c2fe8c1fc8f31177e7be6954f73bbd2a6ac4a95488ad6d36aa6bb281f1a",
    "d55f477550f0616f80d7701eeb7c55e9c7e30590e3e1cdea34f05f2924d393ad",
    # Local-only intermediate lacked an explicit effective-UID-0 preflight.
    "d6eaaec0891d36c60f633a005e4e168ad8d846dbe7fe50cdd77c521d753712e6",
    "0a97c902540917339fac5cb365a9d3c390d6f40a0ab3fe0eda2a18e72aa110b9",
    # Docker declared blkio on the data partition, not the cgroup-v1 parent.
    INVALIDATED_PARENT_THROTTLE_BUNDLE_ID,
    INVALIDATED_QUALIFICATION_CONFIG_BUNDLE_ID,
    INVALIDATED_MATERIALIZER_COLLECTOR_BUNDLE_ID,
    INVALIDATED_PAIRED_AUTHORITY_BUNDLE_ID,
    INVALIDATED_GATE_BINDING_BUNDLE_ID,
    INVALIDATED_MISSING_PRODUCER_BUNDLE_ID,
    INVALIDATED_RAW_EXECUTOR_MISSING_BUNDLE_ID,
    INVALIDATED_COLLECTOR_SUPERSEDED_BUNDLE_ID,
    INVALIDATED_SHOW_STATUS_PARSER_BUNDLE_ID,
    INVALIDATED_UNBOUND_CAPTURE_TRANSITION_BUNDLE_ID,
    A0CC_DYNAMIC_ADOPTION_IDENTITY_BUNDLE_ID,
    CDD36_MISSING_BOOTSTRAP_ROOT_GATE_BUNDLE_ID,
    BED961_CLIENT_ANONYMOUS_VOLUME_BUNDLE_ID,
]
PARAMETERS = [
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "key_compare_cost",
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "memory_temptable_row_cost",
]


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BuildError("regular JSON required: %s" % path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: %s" % path)
    return value


def relative_files(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise BuildError("bundle symlink prohibited: %s" % path)
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def copy_regular_tree(source, destination):
    if source.is_symlink() or not source.is_dir():
        raise BuildError("regular predecessor tree required")
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise BuildError("predecessor symlink prohibited")
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            os.chmod(target, stat.S_IMODE(path.stat().st_mode) | 0o600)


def triple_copy(source, destination, mode=0o755):
    before = sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    after = sha256(source)
    copied = sha256(destination)
    if before != after or after != copied:
        raise BuildError("source changed while sealing: %s" % source)
    os.chmod(destination, mode)
    return copied


def triple_copy_payload(payload, destination, mode=0o755):
    """Seal a generated, reviewable XP-only collector successor."""
    if not isinstance(payload, bytes) or not payload:
        raise BuildError("generated source payload differs")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    digest = hashlib.sha256(payload).hexdigest()
    if sha256(destination) != digest:
        raise BuildError("generated source seal differs")
    os.chmod(destination, mode)
    return digest


def xp_parent_throttle_collector_payload():
    """Derive only the parent-throttle fix from the pinned XP collector.

    The shared collector can change for other scenarios.  This keeps XP's
    provenance at ee9c and refuses to build if that exact source changes.
    """
    if sha256(XP_COLLECTOR_BASE) != BASE_COLLECTOR_SHA256:
        raise BuildError("XP base collector freeze point differs")
    source = XP_COLLECTOR_BASE.read_text(encoding="utf-8")
    start = source.find("def _rate_matches(rows, device, expected):\n")
    end = source.find("\ndef _tmpfs_size_bytes(", start)
    if start < 0 or end < 0 or source.count("def _rate_matches(") != 1:
        raise BuildError("XP base collector throttle region differs")
    replacement = '''def _exact_rate_binding(rows, device, expected):
    """Accept one exact Docker blkio declaration on the throttle parent."""
    return (
        isinstance(rows, list)
        and len(rows) == 1
        and isinstance(rows[0], dict)
        and rows[0].get("Path") == device
        and int(rows[0].get("Rate") or 0) == int(expected)
    )


def _v1_effective_throttle(root, major_minor, read_bps, write_bps):
    """Read effective v1 throttle state; never infer it from partition I/O."""
    def exact(path, label, expected):
        rows = []
        for line in _read_required(path, label).splitlines():
            fields = line.split()
            if len(fields) != 2:
                raise QualificationError("%s row malformed" % label)
            rows.append((fields[0], _integer(fields[1], label + " rate")))
        if rows != [(major_minor, int(expected))]:
            raise QualificationError("%s effective binding differs" % label)
        return {"major_minor": major_minor, "rate": int(expected), "path": str(path)}
    return {
        "read": exact(root / "blkio.throttle.read_bps_device", "cgroup v1 read BPS", read_bps),
        "write": exact(root / "blkio.throttle.write_bps_device", "cgroup v1 write BPS", write_bps),
    }


def validate_container_readback(container_row, runtime):
    envelope = runtime["resource_envelope"]
    storage = runtime["storage"]
    host_config = container_row.get("HostConfig") or {}
    state = container_row.get("State") or {}
    pid = int(state.get("Pid") or 0)
    if state.get("Running") is not True or pid <= 0:
        raise QualificationError("target container has no running workload PID")
    expected_cpu = float(envelope["cpu_quota_cores"])
    quota = int(host_config.get("CpuQuota") or 0)
    period = int(host_config.get("CpuPeriod") or 0)
    if period <= 0 or abs((float(quota) / float(period)) - expected_cpu) > 1e-9:
        raise QualificationError("container CPU quota differs")
    exact = {
        "CpusetCpus": envelope["cpuset_cpus"],
        "CpusetMems": envelope["cpuset_mems"],
        "Memory": envelope["memory_bytes"],
        "MemorySwap": envelope["memory_swap_bytes"],
        "PidsLimit": envelope["pids_max"],
    }
    for key, expected in exact.items():
        actual = host_config.get(key)
        if key.startswith("Cpuset"):
            if expand_cpuset(actual) != expand_cpuset(expected):
                raise QualificationError("container %s differs" % key)
        elif int(actual or 0) != int(expected):
            raise QualificationError("container %s differs" % key)
    data_device = storage["device"]
    throttle_device = storage.get("io_throttle_parent_device")
    throttle_major_minor = storage.get("io_throttle_parent_major_minor")
    if (
        data_device != "/dev/nvme0n1p1"
        or storage.get("expected_live_major_minor") != "259:4"
        or throttle_device != "/dev/nvme0n1"
        or throttle_major_minor != "259:1"
        or data_device == throttle_device
    ):
        raise QualificationError("XP data/throttle-parent contract differs")
    if not _exact_rate_binding(
        host_config.get("BlkioDeviceReadBps"), throttle_device,
        envelope["io_read_bps"],
    ):
        raise QualificationError("container read BPS binding differs")
    if not _exact_rate_binding(
        host_config.get("BlkioDeviceWriteBps"), throttle_device,
        envelope["io_write_bps"],
    ):
        raise QualificationError("container write BPS binding differs")
    roots = _cgroup_roots(pid)
    if roots["version"] != 1:
        raise QualificationError("XP throttle-parent requires cgroup v1")
    effective_throttle = _v1_effective_throttle(
        roots["io"], throttle_major_minor,
        envelope["io_read_bps"], envelope["io_write_bps"],
    )
    status = process_status(pid)
    if expand_cpuset(status.get("Cpus_allowed_list", "")) != expand_cpuset(
        envelope["cpuset_cpus"]
    ):
        raise QualificationError("container PID CPU affinity differs")
    if expand_cpuset(status.get("Mems_allowed_list", "")) != expand_cpuset(
        envelope["cpuset_mems"]
    ):
        raise QualificationError("container PID NUMA policy differs")
    return {
        "container_id": container_row.get("Id"),
        "container_name": container_row.get("Name"),
        "pid": pid,
        "data_partition_device": data_device,
        "data_partition_major_minor": "259:4",
        "throttle_parent_device": throttle_device,
        "throttle_parent_major_minor": throttle_major_minor,
        "effective_cgroup_v1_throttle": effective_throttle,
        "host_config": {
            key: host_config.get(key)
            for key in (
                "CpuQuota", "CpuPeriod", "CpusetCpus", "CpusetMems",
                "Memory", "MemorySwap", "PidsLimit", "BlkioDeviceReadBps",
                "BlkioDeviceWriteBps",
            )
        },
        "pid_cpus_allowed_list": status.get("Cpus_allowed_list"),
        "pid_mems_allowed_list": status.get("Mems_allowed_list"),
    }
'''
    return (source[:start] + replacement + source[end:]).encode("utf-8")


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("bundle symlink prohibited")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def thaw_directories(root):
    if not root.exists() or root.is_symlink():
        return
    for path in [root] + sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            os.chmod(path, stat.S_IMODE(path.stat().st_mode) | 0o700)


def fsync_tree(root):
    for path in sorted(root.rglob("*")):
        if path.is_file():
            descriptor = os.open(str(path), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for path in sorted(
        [root] + [item for item in root.rglob("*") if item.is_dir()],
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def verify_base(base):
    manifest_path = base / "bundle_manifest.json"
    manifest = load(manifest_path)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != base.name
        or hashlib.sha256(canonical(unsigned)).hexdigest() != base.name
        or manifest.get("schema_version")
        != "mysql_algorithm_c_distributed_successor_bundle_v1"
        or manifest.get("scenario") != "xp"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
    ):
        raise BuildError("distributed predecessor differs")
    actual = relative_files(base)
    actual.pop("bundle_manifest.json", None)
    actual.pop("bundle_manifest.json.sha256", None)
    if actual != manifest.get("files"):
        raise BuildError("distributed predecessor closure differs")
    runtime = load(base / "distributed_runtime_contract.json")
    if (
        runtime.get("resource_envelope", {}).get("cpu_quota_cores") != 5.3
        or runtime.get("resource_envelope", {}).get("io_read_bps") != 29360128
        or runtime.get("resource_envelope", {}).get("io_write_bps") != 524288
        or runtime.get("client_resource_envelope", {}).get("tmpfs_bytes")
        != 2147483648
    ):
        raise BuildError("distributed resource envelope differs")
    return manifest, runtime


def executable_runtime_contract(runtime):
    result = json.loads(json.dumps(runtime))
    storage = result.get("storage")
    if (
        not isinstance(storage, dict)
        or storage.get("device") != "/dev/nvme0n1p1"
        or storage.get("expected_live_major_minor") != "259:4"
    ):
        raise BuildError("XP data-partition runtime contract differs")
    storage.update({
        "data_partition": "/dev/nvme0n1p1",
        "data_partition_major_minor": "259:4",
        "io_throttle_parent_device": "/dev/nvme0n1",
        "io_throttle_parent_major_minor": "259:1",
        "docker_bps_declaration_device": "/dev/nvme0n1",
        "effective_cgroup_v1_throttle_major_minor": "259:1",
        "data_partition_and_throttle_parent_must_not_be_conflated": True,
    })
    result["toolchain"]["tree_manifest"] = (
        "runtime/jdk-23.0.2-tree-manifest.json"
    )
    result["database_counter_transport"] = {
        "schema_version":
            "mysql_algorithm_c_database_counter_transport_v2",
        "kind": "exact_client_container_docker_exec_stdout_v1",
        "mysql_binary_in_client": "/usr/bin/mysql",
        "mysql_defaults_file_relative":
            "qualification/config/mysql-client.cnf",
        "mysql_defaults_file_in_client":
            "/opt/mysql-xp-config/mysql-client.cnf",
        "client_bundle_destination": "/opt/mysql-xp-bundle",
        "client_runtime_destination": "/opt/mysql-xp-jdk",
        "client_config_source_relative": "qualification/config",
        "client_config_destination": "/opt/mysql-xp-config",
        "stdout_only": True,
        "host_mysql_cli_required": False,
        "docker_socket_mount_prohibited": True,
        "target_datadir_mount_prohibited": True,
    }
    result["qualification_workload"] = {
        "properties":
            "qualification/config/hybench_xp_qualification.properties",
        "statements":
            "qualification/config/stmt_mysql_formal_case_normalized.toml",
        "mysql_defaults": "qualification/config/mysql-client.cnf",
        "container_config_destination": "/opt/mysql-xp-config",
        "read_only_mount_required_before_client_create": True,
        "manifest_hash_closure_required": True,
        "database": "hybench_sf10x",
        "mysql_port": 65104,
        "warmup_seconds": 60,
        "maximum_measurement_seconds": 600,
        "qualification_measurement_seconds": 180,
        "graceful_quiescence_seconds": 120,
        "workload_seed": 4508333,
        "xapclient": 8,
        "xtpclient": 8,
        "qps_resource_input_present": False,
    }
    result["capture_only_resource_envelope"] = {
        "io_read_bps": 1205862400,
        "io_write_bps": 1205862400,
        "capture_only": True,
        "capture_is_r0": False,
        "accepted_rounds_credit": 0,
        "must_not_replace_formal_resource_envelope": True,
    }
    return result


def contract(base, runtime, hashes):
    storage = dict(runtime["storage"])
    storage.update({
        "data_partition": "/dev/nvme0n1p1",
        "data_partition_major_minor": "259:4",
        "io_throttle_parent_device": "/dev/nvme0n1",
        "io_throttle_parent_major_minor": "259:1",
        "unique_partition_required": True,
        "parent_other_mount_holder_or_swap_prohibited": True,
    })
    return {
        "schema_version": "mysql_xp_algorithm_c_executable_launch_contract_v3",
        "scenario": "hybench_xp_io",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "base_successor_id": base.name,
        "superseded_static_bundle_id": SUPERSEDED_ID,
        "superseded_static_bundle_launch_authorized": False,
        "database_resource_envelope": runtime["resource_envelope"],
        "client_resource_envelope": runtime["client_resource_envelope"],
        "distributed_host_contract": {
            "host_id": "node02",
            "scope": "mysql_only",
            "database_cpuset_cpus": "21-33,57-69",
            "database_cpuset_mems": "1",
            "client_cpuset_cpus": "3-11,39-47",
            "client_cpuset_mems": "0",
            "host_local_cpu_numbering": True,
            "compute00_reserved_mysql_xp_cpuset": "184-199,440-455",
            "compute00_cpuset_is_not_valid_for_node02": True,
            "cpuset_change_requires_main_agent_authority": True,
            "postgresql_mutation_prohibited": True,
        },
        "storage": storage,
        "collector_throttle_binding": {
            "data_partition": "/dev/nvme0n1p1",
            "data_partition_major_minor": "259:4",
            "docker_bps_declaration_device": "/dev/nvme0n1",
            "effective_cgroup_v1_throttle_major_minor": "259:1",
            "io_read_bps": 29360128,
            "io_write_bps": 524288,
            "exact_single_docker_binding_required": True,
            "exact_effective_v1_binding_required": True,
            "data_partition_and_throttle_parent_must_not_be_conflated": True,
        },
        "toolchain": runtime["toolchain"],
        "collector_sha256": hashes[
            "qualification/mysql_distributed_live_readback_qualification_v1.py"
        ],
        "adopted_service": {
            "path": "launch/mysql_xp_node02_adopted_service_v1.py",
            "sha256": hashes[
                "launch/mysql_xp_node02_adopted_service_v1.py"
            ],
            "host": "node02",
            "no_ssh": True,
            "physical_copy_argv":
                ["cp", "-a", "--", "SOURCE/.", "TARGET"],
            "unsupported_reflink_option_prohibited": True,
            "per_file_sha256_required": True,
            "fiemap_shared_extent_rejection_required": True,
            "fiemap_source_target_overlap_rejection_required": True,
            "database_and_client_images_exact_local_id_pinned": True,
            "image_tag_provenance": "mysql:8.0",
            "required_image_id":
                "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172",
            "registry_digest_required": False,
            "empty_repo_digests_supported": True,
            "short_lived_main_agent_token_required_for_bootstrap": True,
            "short_lived_authority_checked_on_remote_before_create": True,
            "manifest_covered_sealed_config_required": True,
            "qualification_config_directory":
                "qualification/config",
            "qualification_config_exact_files": sorted(
                Path(relative).name
                for relative in QUALIFICATION_CONFIG_SOURCES
            ),
            "qualification_config_mounted_read_only_before_client_create":
                True,
            "expiry_rechecked_immediately_before_create": True,
            "host_output_tmpfs_checked_before_create": True,
            "frozen_port_checked_without_host_lsof_or_ss_before_create": True,
            "copy_runtime_socket_policy":
                "exclude_exact_datadir_mysql_sock_never_follow_never_copy",
            "copy_source_is_template_datadir_contents": True,
            "copy_process_cpu_affinity_enforced": "21-33,57-69",
            "host_tmpfs_readback":
                "proc_self_mountinfo_plus_statvfs_v1",
            "legacy_findmnt_byte_flag_dependency": False,
            "database_bind_mount_create_mode":
                "default_writable_without_bare_rw_field",
            "database_bind_mount_inspect_rw_required": True,
            "data_path_partition_identity_required":
                "/dev/nvme0n1p1@259:4",
            "cgroup_v1_throttle_parent_identity_required":
                "/dev/nvme0n1@259:1",
            "unique_partition_and_parent_exclusivity_gate_required": True,
            "attempt_datadir_owner": {"uid": 999, "gid": 999},
            "attempt_datadir_directory_mode": 448,
            "attempt_datadir_regular_mode": 384,
            "permission_normalization_scope":
                "attempt_owned_datadir_only_after_physical_copy",
            "permission_normalization_effective_uid_required": 0,
            "bootstrap_pristine_readback_effective_uid_required": 0,
            "bootstrap_euid_gate_before_authority_or_datadir_read": True,
            "source_metadata_before_after_identity_required": True,
            "pristine_target_metadata_and_content_rechecked_before_create":
                True,
            "all_sealed_entrypoints_disable_bytecode_at_start": True,
            "host_python_required_argv":
                ["env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B"],
            "prepare_copy_bundle_closure_before_after_required": True,
            "sealed_bundle_pycache_prohibited": True,
            "client_root_read_only": True,
            "client_private_tmpfs_bytes": 2147483648,
            "host_output_tmpfs_not_client_mounted": True,
            "client_image_declared_mysql_volume_mask": {
                "source": "client_masks/var_lib_mysql",
                "destination": "/var/lib/mysql",
                "read_only": True,
                "type": "bind",
                "marker":
                    "client_masks/var_lib_mysql/.sealed-read-only-mask",
            },
            "client_mount_destination_set_exact_required": True,
            "client_anonymous_volume_prohibited": True,
            "client_pg_nvme2_mount_source_prohibited": True,
            "host_mysql_binary_prohibited": True,
            "sql_transport":
                "collector_managed_client_docker_exec_stdout_v1",
            "static_inspect_identity": {
                "schema_version": "mysql_xp_static_inspect_identity_v2",
                "hash_inputs": [
                    "entire_Config_object",
                    "entire_HostConfig_object",
                    "strict_declared_Mounts_projection",
                    "HostConfig.NetworkMode",
                    "HostConfig.PortBindings",
                ],
                "dynamic_NetworkSettings_in_static_hash": False,
                "dynamic_NetworkSettings_observed_and_diffed": True,
                "unknown_mount_field_policy": "FAIL_CLOSED",
            },
        },
        "adoption_reconciliation": {
            "path": "launch/mysql_xp_adoption_reconciliation_v1.py",
            "sha256": hashes[
                "launch/mysql_xp_adoption_reconciliation_v1.py"
            ],
            "two_read_only_snapshots_required": True,
            "static_field_diff_policy": "FAIL_CLOSED",
            "same_live_container_id_required": True,
            "dynamic_network_settings_policy":
                "record_and_diff_but_exclude_from_static_identity",
            "evidence_only_no_process_launch": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        },
        "runtime_publication": {
            "publisher":
                "runtime/mysql_xp_node02_jdk_publish_v1.py",
            "publisher_sha256": hashes[
                "runtime/mysql_xp_node02_jdk_publish_v1.py"
            ],
            "tree_manifest":
                "runtime/jdk-23.0.2-tree-manifest.json",
            "tree_content_sha256":
                "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c",
            "java_binary_sha256":
                "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c",
            "content_addressed": True,
            "atomic_nonoverwriting_publish": True,
            "exact_tree_readback_gate_required": True,
            "tree_modes_preserved_as_manifested": True,
            "client_mount_read_only_required": True,
            "unbound_python_runtime_prohibited": True,
        },
        "promotion_materializer": {
            "path":
                "launch/mysql_xp_node02_formal10_materializer_v1.py",
            "sha256": hashes[
                "launch/mysql_xp_node02_formal10_materializer_v1.py"
            ],
            "ordered_stages": [
                "AT1_AT6_real_trace_registry",
                "fresh_R0",
                "127_candidates_fixed_Top5_debug",
                "same_live_formal10_handoff",
            ],
            "evidence_only_no_process_launch": True,
            "same_live_database_and_client_required": True,
        },
        "formal_evidence_producer": {
            "path": "launch/mysql_xp_formal_evidence_producer_v1.py",
            "sha256": hashes[
                "launch/mysql_xp_formal_evidence_producer_v1.py"
            ],
            "produced_schemas": [
                "mysql_xp_production_at_capture_v3",
                "mysql_xp_fresh_r0_qualification_v3",
            ],
            "evidence_only_no_process_launch": True,
            "live_adopted_database_and_client_pid_starttime_required": True,
            "capture_at1_at6_and_worker_future_validation_required": True,
            "formal_envelope_restoration_readback_required": True,
            "fresh_r0_180_sample_counter_cadence_required": True,
            "database_counter_missing_or_timeout_result": "FAIL",
            "capture_credit_as_r0_or_accepted": 0,
        },
        "raw_evidence_executor": {
            "path": "launch/mysql_xp_raw_evidence_executor_v1.py",
            "sha256": hashes["launch/mysql_xp_raw_evidence_executor_v1.py"],
            "actual_sealed_java_workload_required": True,
            "java_jar_mode_prohibited": True,
            "classpath_first_entry": "qualification/native_latency_producer/classes",
            "sealed_trace_jar": (
                "predecessor/predecessor/HyBench-1.0-SNAPSHOT-formal-xp-"
                "binding-trace-candidate-jar-with-dependencies.jar"
            ),
            "capture_requires_capture_io_then_formal_restore_readback": True,
            "capture_ledger_requires_adopted_client_cgroup_identity": True,
            "fresh_r0_requires_180_real_pid_starttime_counter_samples": True,
            "counter_missing_or_timeout_result": "FAIL",
            "fresh_r0_graceful_quiescence_seconds": 120,
            "raw_run_is_capture_or_r0_or_accepted_credit": 0,
            "formal_launch_authorized": False,
        },
        "capture_transition": {
            "path": "launch/mysql_xp_capture_transition_v1.py",
            "sha256": hashes["launch/mysql_xp_capture_transition_v1.py"],
            "host": "node02",
            "database_blkio_parent_major_minor": "259:1",
            "capture_io_bps_per_direction": 1205862400,
            "formal_io_read_bps": 29360128,
            "formal_io_write_bps": 524288,
            "transactional_readback_and_restore_required": True,
            "capture_workload_must_run_in_adopted_client_cgroup": True,
            "capture_r0_formal_accepted_authority": False,
        },
        "runtime_entrypoints": [
            "launch/mysql_xp_node02_adopted_service_v1.py",
            "launch/mysql_xp_capture_authority_v1.py",
            "launch/mysql_xp_capture_transition_v1.py",
            "launch/mysql_xp_node02_formal10_materializer_v1.py",
            "launch/mysql_xp_formal_evidence_producer_v1.py",
            "launch/mysql_xp_raw_evidence_executor_v1.py",
            "runtime/mysql_xp_node02_jdk_publish_v1.py",
            "qualification/mysql_distributed_live_readback_qualification_v1.py",
            "qualification/mysql_distributed_workload_metric_adapter_v1.py",
            "qualification/mysql_xp_qualification_authority_v1.py",
        ],
        "qualification_authority": {
            "materializer":
                "qualification/mysql_xp_qualification_authority_v1.py",
            "debug_only": True,
            "main_agent_token_file_required": True,
            "same_live_adopted_service_required": True,
            "command_spec_and_authority_sha256_sidecars_required": True,
            "capture_r0_formal_accepted_authority": False,
        },
        "capture_authority": {
            "materializer": "launch/mysql_xp_capture_authority_v1.py",
            "sha256": hashes["launch/mysql_xp_capture_authority_v1.py"],
            "same_live_adopted_service_required": True,
            "main_agent_token_file_required": True,
            "attempt_owned_host_tmpfs_output_required": True,
            "runtime_authority_schema": "xp_runtime_authority_bundle_v1",
            "capture_authorization_schema": (
                "mysql_xp_capture_launch_authorization_v1"
            ),
            "capture_io_bps_per_direction": 1205862400,
            "capture_r0_formal_accepted_authority": False,
        },
        "bidirectional_qualification_gate": {
            "collector": (
                "qualification/mysql_distributed_live_readback_"
                "qualification_v1.py"
            ),
            "collector_sha256": hashes[
                "qualification/mysql_distributed_live_readback_"
                "qualification_v1.py"
            ],
            "collector_mode": "--compare-bidirectional",
            "requires_four_distinct_attempt_evidences": [
                "scenario_a_solo", "scenario_a_paired",
                "scenario_b_solo", "scenario_b_paired",
            ],
            "peer_selected_at_launch": True,
            "reciprocal_peer_binding_required": True,
            "no_preselected_postgresql_peer": True,
            "materializer": (
                "launch/mysql_xp_node02_formal10_materializer_v1.py"
            ),
            "materializer_stage": "gate",
            "materializer_evidence_only_no_process_launch": True,
            "same_collector_and_180_second_gate_required": True,
        },
        "capture_only_resource_envelope":
            runtime["capture_only_resource_envelope"],
        "nonruntime_provenance_only": [
            "build_mysql_xp_formal10_launch_v3.py",
            "predecessor/",
        ],
        "algorithm": {
            "method": "experience_table_sgd",
            "active_parameters": PARAMETERS,
            "active_parameter_count": 7,
            "required_nonempty_subsets": 127,
            "subset_enumeration": "all_2^7_minus_1_nonempty_subspaces",
            "candidate_order": "global_descending_final_candidate_score",
            "fixed_real_replay_top_k": 5,
            "target_accepted_rounds": 10,
            "original_gradient_only": True,
            "gradient_formula":
                "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
            "parameter_update_source": "g_j_and_eta_only",
            "required_gradient_event_fields": [
                "feature_ratio", "predicted_resource", "utilization",
                "dScore/dU", "T", "confidence", "g_j", "eta",
                "delta_param", "candidate_score",
            ],
            "qps_role": "real_replay_outer_acceptance_only",
            "replacement_search_algorithm_prohibited": True,
        },
        "fingerprint": {
            "l1_l2_l3_required": True,
            "xp_l3": sorted([
                "shared_read_blocks", "shared_write_blocks",
                "local_read_blocks", "local_write_blocks",
                "temp_read_blocks", "temp_write_blocks", "io_wait_ms",
            ]),
            "duplicate_rule":
                "exact_parameter_signature_and_equal_l1_and_tolerant_l2_l3",
            "missing_any_level_policy": "independent_candidate",
        },
        "graceful_quiescence_seconds": 120,
        "stable_window_seconds": 180,
        "hard_upper_bound_seconds": 600,
        "capture_r0_debug_failed_replay_accepted_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def verify_bundle(bundle, expected_id=None):
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load(manifest_path)
    sidecar = bundle / "bundle_manifest.json.sha256"
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        (expected_id is not None and bundle_id != expected_id)
        or (expected_id is None and bundle_id != bundle.name)
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
        or manifest.get("schema_version")
        != "mysql_xp_distributed_formal10_launch_v3"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or sidecar.read_text(encoding="ascii")
        != "%s  bundle_manifest.json\n" % sha256(manifest_path)
    ):
        raise BuildError("published bundle identity differs")
    actual = relative_files(bundle)
    actual.pop("bundle_manifest.json", None)
    actual.pop("bundle_manifest.json.sha256", None)
    if actual != manifest.get("files"):
        raise BuildError("published bundle closure differs")
    for path in [bundle] + sorted(bundle.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published bundle is not frozen")
    return manifest


def publish(base=BASE, output_root=OUTPUT_ROOT):
    base = Path(base).resolve()
    output_root = Path(output_root).resolve()
    unused_manifest, base_runtime = verify_base(base)
    runtime = executable_runtime_contract(base_runtime)
    if (
        SUPERSEDED.name != SUPERSEDED_ID
        or sha256(SUPERSEDED / "bundle_manifest.json")
        != "f2d9aa191053ef0e19a26e39d472fa56100f058ee787b0a749263a815fec6cd8"
    ):
        raise BuildError("superseded static bundle identity differs")
    if (
        INVALIDATED_LAUNCH.name
        != "7d1fa34305637124dcbb4d1f97a91d77ee6f9810a5aa4ae229a8433de2b4df0f"
        or sha256(INVALIDATED_LAUNCH / "bundle_manifest.json")
        != "4560505cf151f8995711810410a902af8ec515fdf78cc243a06e7f9b801fc05e"
    ):
        raise BuildError("invalidated image-reference bundle identity differs")
    if (
        INVALIDATED_TOKENLESS_LAUNCH.name
        != "fd5fd07dcf42daf590fb08ea6f88394531eac982ead2052ec348e27f7f0ec2df"
        or sha256(INVALIDATED_TOKENLESS_LAUNCH / "bundle_manifest.json")
        != "c16a3795bdabd2e52038eb5d7970670eb97b80355835c5ca1bbf83ea34ab2d6b"
    ):
        raise BuildError("invalidated tokenless bundle identity differs")
    if (
        INVALIDATED_UNEXPIRING_LAUNCH.name
        != "fe1bcc7a8624e3fb1f12092c50e37216d730b2f2dfe55360c4cccfb631db0fb1"
        or sha256(INVALIDATED_UNEXPIRING_LAUNCH / "bundle_manifest.json")
        != "ce2730d0b78bf4b387b2c1d36d5ef958d8557f7e010274bdb29982827ca8eaa4"
    ):
        raise BuildError("invalidated unexpiring bundle identity differs")
    if (
        INVALIDATED_UNBOUND_CONFIG_LAUNCH.name
        != "ad6c994c2ee4101d67f893558ef5be87712c5fbd40c417670631d84a95b6b08c"
        or sha256(INVALIDATED_UNBOUND_CONFIG_LAUNCH / "bundle_manifest.json")
        != "3394faca022c597693f13b563bc3fa630ce940ec1c55c894aea2e194710e33ba"
    ):
        raise BuildError("invalidated unbound-config bundle identity differs")
    if (
        INVALIDATED_PRECREATE_GATE_LAUNCH.name
        != "7f41936b910a6b87e126ec11507c8e4e47c7e95b2259345fe95b3807400829c6"
        or sha256(INVALIDATED_PRECREATE_GATE_LAUNCH / "bundle_manifest.json")
        != "6120b69d6f61b957584760335da2b571dbebe441798620cb5913d7dde0546ffb"
    ):
        raise BuildError("invalidated precreate-gate bundle identity differs")
    if (
        INVALIDATED_PORT_GATE_LAUNCH.name
        != "4130d5dcf5b08732f352833dc7ea7bb4b599f4e008477b82f44a76ff64d1b14f"
        or sha256(INVALIDATED_PORT_GATE_LAUNCH / "bundle_manifest.json")
        != "400ba08f246afe647dfecb4cac254066463a2f8514f3b4db1ee2da22dc9e956e"
    ):
        raise BuildError("invalidated port-gate bundle identity differs")
    if (
        INVALIDATED_RUNTIME_SOCKET_LAUNCH.name
        != "637cf3a8d9e7fe85e979b846304a4cdb823f7566bcf6b17760d8c4b4c80bf503"
        or sha256(INVALIDATED_RUNTIME_SOCKET_LAUNCH / "bundle_manifest.json")
        != "34da8821e7172016c4362ed6a9e5cea0da38f8a588e9bd6bccffc31569021b70"
    ):
        raise BuildError("invalidated runtime-socket bundle identity differs")
    if (
        INVALIDATED_LEGACY_FINDMNT_LAUNCH.name
        != "96d938614f88e37445d56a3c0d4eeef960e6d0990f7df56c85aca3c11480fceb"
        or sha256(INVALIDATED_LEGACY_FINDMNT_LAUNCH / "bundle_manifest.json")
        != "364296b32a8deea29fc8ecdf7665b943cb492b0a23009e1266b021483c6d143b"
    ):
        raise BuildError("invalidated legacy-findmnt bundle identity differs")
    if (
        INVALIDATED_LEGACY_DOCKER_MOUNT_LAUNCH.name
        != "3599cd89ad2950039bb2c685d9f3edf540e23ceae715fb4ccac42f41bb772111"
        or sha256(
            INVALIDATED_LEGACY_DOCKER_MOUNT_LAUNCH / "bundle_manifest.json"
        )
        != "2588977127fa609782d0028695744517d639ea57d13732952a32a9e98f51ad7d"
    ):
        raise BuildError(
            "invalidated legacy-Docker mount bundle identity differs"
        )
    if (
        INVALIDATED_PARTITION_THROTTLE_LAUNCH.name
        != "79815c2fe8c1fc8f31177e7be6954f73bbd2a6ac4a95488ad6d36aa6bb281f1a"
        or sha256(
            INVALIDATED_PARTITION_THROTTLE_LAUNCH / "bundle_manifest.json"
        )
        != "d54837fb2bfa369099d2fc1f67f39fb34e13bd71d5a7e92bf1822bb579ea35b5"
    ):
        raise BuildError(
            "invalidated partition-throttle bundle identity differs"
        )
    if (
        INVALIDATED_UNWRITABLE_DATADIR_LAUNCH.name
        != "d55f477550f0616f80d7701eeb7c55e9c7e30590e3e1cdea34f05f2924d393ad"
        or sha256(
            INVALIDATED_UNWRITABLE_DATADIR_LAUNCH / "bundle_manifest.json"
        )
        != "7240b35d6384bae0930a061248c6d3f4fe9388aba9b56d5d1a568f973fe4e9e7"
    ):
        raise BuildError(
            "invalidated unwritable-datadir bundle identity differs"
        )
    if (
        INVALIDATED_BYTECODE_POLLUTED_LAUNCH.name
        != "0a97c902540917339fac5cb365a9d3c390d6f40a0ab3fe0eda2a18e72aa110b9"
        or sha256(
            INVALIDATED_BYTECODE_POLLUTED_LAUNCH / "bundle_manifest.json"
        )
        != "ff6f408040a9df94ce5e92b899115fab2976067864a3c93d2c3a7042f1e0a6ca"
    ):
        raise BuildError(
            "invalidated bytecode-polluted bundle identity differs"
        )
    a0cc = OUTPUT_ROOT / A0CC_DYNAMIC_ADOPTION_IDENTITY_BUNDLE_ID
    if (
        a0cc.name != A0CC_DYNAMIC_ADOPTION_IDENTITY_BUNDLE_ID
        or sha256(a0cc / "bundle_manifest.json")
        != A0CC_DYNAMIC_ADOPTION_IDENTITY_MANIFEST_SHA256
    ):
        raise BuildError("a0cc dynamic adoption identity bundle differs")
    cdd36 = OUTPUT_ROOT / CDD36_MISSING_BOOTSTRAP_ROOT_GATE_BUNDLE_ID
    if (
        cdd36.name != CDD36_MISSING_BOOTSTRAP_ROOT_GATE_BUNDLE_ID
        or sha256(cdd36 / "bundle_manifest.json")
        != CDD36_MISSING_BOOTSTRAP_ROOT_GATE_MANIFEST_SHA256
    ):
        raise BuildError("cdd36 bootstrap root-gate bundle differs")
    bed961 = OUTPUT_ROOT / BED961_CLIENT_ANONYMOUS_VOLUME_BUNDLE_ID
    if (
        bed961.name != BED961_CLIENT_ANONYMOUS_VOLUME_BUNDLE_ID
        or sha256(bed961 / "bundle_manifest.json")
        != BED961_CLIENT_ANONYMOUS_VOLUME_MANIFEST_SHA256
    ):
        raise BuildError("bed961 client anonymous-volume bundle differs")
    # Derive the XP-only collector before publication.  No shared collector
    # checkout is an input to this successor.
    collector_payload = xp_parent_throttle_collector_payload()
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".xp-launch-v3.", dir=output_root))
    try:
        copy_regular_tree(base, stage / "predecessor")
        hashes = {
            relative: triple_copy(source, stage / relative)
            for relative, source in sorted(SOURCES.items())
        }
        hashes[XP_COLLECTOR_RELATIVE] = triple_copy_payload(
            collector_payload, stage / XP_COLLECTOR_RELATIVE
        )
        hashes.update({
            relative: triple_copy(source, stage / relative, mode=0o644)
            for relative, source in sorted(
                QUALIFICATION_CONFIG_SOURCES.items()
            )
        })
        triple_copy(
            base / "toolchain/jdk-23.0.2-tree-manifest.json",
            stage / "runtime/jdk-23.0.2-tree-manifest.json",
        )
        (stage / "distributed_runtime_contract.json").write_bytes(
            canonical(runtime)
        )
        triple_copy(BUILDER, stage / "build_mysql_xp_formal10_launch_v3.py")
        superseded = {
            "schema_version": "mysql_xp_static_bundle_invalidation_v1",
            "status": "SUPERSEDED_DO_NOT_LAUNCH",
            "bundle_id": SUPERSEDED_ID,
            "bundle_manifest_sha256": sha256(
                SUPERSEDED / "bundle_manifest.json"
            ),
            "reason":
                "static_only_missing_node02_executable_launch_closure",
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (stage / "superseded_static_bundle.json").write_bytes(
            canonical(superseded)
        )
        invalidated_launch = {
            "schema_version":
                "mysql_xp_image_reference_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_LAUNCH / "bundle_manifest.json"
            ),
            "reason":
                "repo_digests_empty_and_fake_name_at_image_id_is_not_executable",
            "observed_tag": "mysql:8.0",
            "observed_image_id":
                "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172",
            "observed_repo_digests": [],
            "replacement":
                "docker_create_by_exact_local_image_id_with_inspect_readback",
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (stage / "invalidated_image_reference_bundle.json").write_bytes(
            canonical(invalidated_launch)
        )
        invalidated_tokenless = {
            "schema_version":
                "mysql_xp_tokenless_bootstrap_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_TOKENLESS_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_TOKENLESS_LAUNCH / "bundle_manifest.json"
            ),
            "reason":
                "bootstrap_mutation_was_not_bound_to_short_lived_main_agent_secret",
            "replacement":
                "sha256_bound_main_agent_token_required_before_container_create",
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (stage / "invalidated_tokenless_bundle.json").write_bytes(
            canonical(invalidated_tokenless)
        )
        invalidated_unexpiring = {
            "schema_version":
                "mysql_xp_unexpiring_remote_bootstrap_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_UNEXPIRING_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_UNEXPIRING_LAUNCH / "bundle_manifest.json"
            ),
            "reason":
                "expiry_was_checked_only_when_emitting_plan_not_on_node02",
            "replacement":
                "remote_service_revalidates_created_and_expiry_before_create",
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (stage / "invalidated_unexpiring_bundle.json").write_bytes(
            canonical(invalidated_unexpiring)
        )
        invalidated_unbound_config = {
            "schema_version":
                "mysql_xp_unbound_config_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_UNBOUND_CONFIG_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_UNBOUND_CONFIG_LAUNCH / "bundle_manifest.json"
            ),
            "reason":
                "service_accepted_arbitrary_existing_config_directory",
            "replacement":
                "manifest_covered_sealed_bundle_config_is_mandatory",
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (stage / "invalidated_unbound_config_bundle.json").write_bytes(
            canonical(invalidated_unbound_config)
        )
        invalidated_precreate_gate = {
            "schema_version":
                "mysql_xp_precreate_gate_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_PRECREATE_GATE_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_PRECREATE_GATE_LAUNCH / "bundle_manifest.json"
            ),
            "reason":
                "expiry_and_host_tmpfs_were_not_rechecked_immediately_before_create",
            "replacement":
                "all_noncontainer_gates_pass_before_first_docker_create",
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (stage / "invalidated_precreate_gate_bundle.json").write_bytes(
            canonical(invalidated_precreate_gate)
        )
        invalidated_port_gate = {
            "schema_version":
                "mysql_xp_port_gate_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_PORT_GATE_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_PORT_GATE_LAUNCH / "bundle_manifest.json"
            ),
            "reason":
                "frozen_host_port_was_not_checked_before_first_docker_create",
            "replacement":
                "python_socket_bind_preflight_without_host_lsof_or_ss",
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (stage / "invalidated_port_gate_bundle.json").write_bytes(
            canonical(invalidated_port_gate)
        )
        invalidated_runtime_socket = {
            "schema_version":
                "mysql_xp_runtime_socket_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_RUNTIME_SOCKET_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_RUNTIME_SOCKET_LAUNCH / "bundle_manifest.json"
            ),
            "reason": [
                "template_runtime_mysql_sock_symlink_was_rejected",
                "template_root_would_have_been_nested_under_target_datadir",
                "host_copy_process_cpu_affinity_was_not_enforced",
            ],
            "replacement": [
                "exclude_exact_datadir_mysql_sock_without_follow_or_copy",
                "copy_only_template_datadir_contents",
                "require_taskset_21_33_57_69",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (stage / "invalidated_runtime_socket_bundle.json").write_bytes(
            canonical(invalidated_runtime_socket)
        )
        invalidated_legacy_findmnt = {
            "schema_version":
                "mysql_xp_legacy_findmnt_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_LEGACY_FINDMNT_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_LEGACY_FINDMNT_LAUNCH
                / "bundle_manifest.json"
            ),
            "reason":
                "node02_util_linux_2_23_findmnt_has_no_byte_output_flag",
            "replacement":
                "exact_proc_mountinfo_target_type_options_plus_statvfs_bytes",
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (stage / "invalidated_legacy_findmnt_bundle.json").write_bytes(
            canonical(invalidated_legacy_findmnt)
        )
        invalidated_legacy_docker_mount = {
            "schema_version":
                "mysql_xp_legacy_docker_mount_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_LEGACY_DOCKER_MOUNT_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_LEGACY_DOCKER_MOUNT_LAUNCH
                / "bundle_manifest.json"
            ),
            "reason":
                "docker_20_10_21_rejects_bare_rw_in_mount_key_value_syntax",
            "replacement": [
                "omit_bare_rw_because_bind_mount_default_is_writable",
                "require_exact_mounts_rw_true_from_docker_inspect",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_legacy_docker_mount_bundle.json"
        ).write_bytes(canonical(invalidated_legacy_docker_mount))
        invalidated_partition_throttle = {
            "schema_version":
                "mysql_xp_partition_throttle_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_PARTITION_THROTTLE_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_PARTITION_THROTTLE_LAUNCH
                / "bundle_manifest.json"
            ),
            "reason":
                "cgroup_v1_rejects_partition_blkio_throttle_at_container_start",
            "observed": {
                "data_partition": "/dev/nvme0n1p1",
                "data_partition_major_minor": "259:4",
                "exclusive_parent_device": "/dev/nvme0n1",
                "exclusive_parent_major_minor": "259:1",
                "container_pid": 0,
            },
            "replacement": [
                "keep_data_mount_identity_bound_to_partition",
                "bind_blkio_throttle_and_inspect_to_exclusive_parent",
                "fail_closed_on_nonunique_partition_or_parent_other_use",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_partition_throttle_bundle.json"
        ).write_bytes(canonical(invalidated_partition_throttle))
        invalidated_unwritable_datadir = {
            "schema_version":
                "mysql_xp_unwritable_datadir_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_UNWRITABLE_DATADIR_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_UNWRITABLE_DATADIR_LAUNCH
                / "bundle_manifest.json"
            ),
            "reason":
                "same_live_database_exited_because_ibdata1_was_not_writable",
            "observed": {
                "database_log":
                    "ibdata1 must be writable",
                "same_live_adoption": "REJECTED",
                "accepted_rounds_credit": 0,
            },
            "replacement": [
                "normalize_only_attempt_owned_datadir_to_uid_gid_999_999",
                "directories_0700_regular_files_0600",
                "prove_source_metadata_before_after_unchanged",
                "recheck_pristine_target_metadata_before_container_create",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_unwritable_datadir_bundle.json"
        ).write_bytes(canonical(invalidated_unwritable_datadir))
        invalidated_bytecode_polluted = {
            "schema_version":
                "mysql_xp_bytecode_polluted_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_BYTECODE_POLLUTED_LAUNCH.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_BYTECODE_POLLUTED_LAUNCH
                / "bundle_manifest.json"
            ),
            "reason": [
                "root_helper_host_mount_namespace_removed_container_ro_overlay",
                "host_python_created_runtime___pycache___inside_sealed_bundle",
                "full_bundle_closure_readback_rejected_unmapped_writable_entry",
            ],
            "observed": {
                "database_container_created": False,
                "client_container_created": False,
                "accepted_rounds_credit": 0,
            },
            "replacement": [
                "every_sealed_entrypoint_sets_sys_dont_write_bytecode",
                "root_helper_uses_env_PYTHONDONTWRITEBYTECODE_1_python3_B",
                "prepare_copy_rechecks_bundle_closure_before_authority",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_bytecode_polluted_bundle.json"
        ).write_bytes(canonical(invalidated_bytecode_polluted))
        invalidated_qualification_config = {
            "schema_version":
                "mysql_xp_qualification_config_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_QUALIFICATION_CONFIG_BUNDLE_ID,
            "bundle_manifest_sha256":
                INVALIDATED_QUALIFICATION_CONFIG_MANIFEST_SHA256,
            "reason": [
                "same_live_client_read_only_config_mount_lacked_runnable_"
                "hybench_workload_properties",
                "qualification_command_could_not_bind_config_statement_and_"
                "mysql_defaults_to_manifest_hash_closure",
                "collector_rejected_launch_v3_bundle_schema_and_had_no_"
                "client_container_database_counter_transport",
            ],
            "observed_same_live_service": {
                "database_pid": 64503,
                "client_pid": 64645,
                "r0_credit": 0,
                "accepted_rounds_credit": 0,
            },
            "replacement": [
                "precreate_client_with_manifest_covered_read_only_"
                "qualification_config_mount",
                "materialize_exact_debug_command_and_short_lived_main_agent_"
                "authority_without_replacing_same_live_service",
                "sample_database_counters_through_exact_client_container_"
                "stdout_bridge",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_qualification_config_bundle.json"
        ).write_bytes(canonical(invalidated_qualification_config))
        invalidated_materializer_collector = {
            "schema_version":
                "mysql_xp_materializer_collector_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_MATERIALIZER_COLLECTOR_BUNDLE_ID,
            "bundle_manifest_sha256":
                INVALIDATED_MATERIALIZER_COLLECTOR_MANIFEST_SHA256,
            "reason": [
                "fresh_r0_materializer_hardcoded_predecessor_collector_hash",
                "sealed_collector_and_materializer_required_hash_differed",
                "valid_fresh_r0_would_fail_closed_before_debug_promotion",
            ],
            "observed": {
                "sealed_collector_sha256": BASE_COLLECTOR_SHA256,
                "materializer_required_collector_sha256":
                    "f3b367e839e365edf3169e77070c3fd0072d3ccdcdcd8c9798077573a1fcf781",
                "database_container_created": False,
                "client_container_created": False,
                "r0_credit": 0,
                "accepted_rounds_credit": 0,
            },
            "replacement": [
                "derive_collector_identity_from_verified_bundle_manifest",
                "require_manifest_file_entry_and_live_collector_hash_match",
                "propagate_same_verified_hash_to_formal10_handoff",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_materializer_collector_bundle.json"
        ).write_bytes(canonical(invalidated_materializer_collector))
        invalidated_parent_throttle = {
            "schema_version": "mysql_xp_parent_throttle_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_PARENT_THROTTLE_BUNDLE_ID,
            "bundle_manifest_sha256": INVALIDATED_PARENT_THROTTLE_MANIFEST_SHA256,
            "attempt_id": "xp-formal-qual-20260731T013531Z-9d452cf46907c403f3050ffc",
            "failure_phase": "pre_workload_live_readback",
            "reason": [
                "docker_blkio_declaration_was_checked_against_data_partition",
                "cgroup_v1_effective_throttle_parent_was_not_collected",
                "data_partition_259_4_and_parent_259_1_were_conflated",
            ],
            "required_replacement": {
                "data_partition": "/dev/nvme0n1p1",
                "data_partition_major_minor": "259:4",
                "docker_bps_declaration_device": "/dev/nvme0n1",
                "effective_cgroup_v1_throttle_major_minor": "259:1",
                "io_read_bps": 29360128,
                "io_write_bps": 524288,
                "exact_single_docker_binding_required": True,
                "exact_effective_v1_binding_required": True,
            },
            "capture_credit": 0,
            "r0_credit": 0,
            "accepted_rounds_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        (stage / "invalidated_parent_throttle_bundle.json").write_bytes(
            canonical(invalidated_parent_throttle)
        )
        invalidated_paired_authority = {
            "schema_version":
                "mysql_xp_paired_authority_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_PAIRED_AUTHORITY_BUNDLE_ID,
            "bundle_manifest_sha256":
                INVALIDATED_PAIRED_AUTHORITY_MANIFEST_SHA256,
            "reason": [
                "paired_authority_required_peer_metric_leaf_to_preexist",
                "both_mutually_bound_output_leaves_required_fresh_creation",
                "sequential_authority_materialization_was_impossible",
            ],
            "observed": {
                "database_container_created": False,
                "client_container_created": False,
                "qualification_credit": 0,
                "r0_credit": 0,
                "accepted_rounds_credit": 0,
            },
            "replacement": [
                "bind_exact_workload_metrics_filename",
                "bind_peer_output_leaf_name_to_peer_attempt_id",
                "permit_absent_leaf_only_when_immediate_ancestor_is_real_dir",
                "continue_rejecting_symlinked_leaf_parent_or_ancestor",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_paired_authority_bundle.json"
        ).write_bytes(canonical(invalidated_paired_authority))
        invalidated_gate_binding = {
            "schema_version":
                "mysql_xp_dynamic_gate_binding_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_GATE_BINDING_BUNDLE_ID,
            "bundle_manifest_sha256":
                INVALIDATED_GATE_BINDING_MANIFEST_SHA256,
            "reason": [
                "generic_bidirectional_comparator_output_lacked_bundle_id",
                "generic_gate_lacked_adopted_same_live_identity_binding",
                "generic_and_xp_materializer_gate_field_names_differed",
            ],
            "observed": {
                "database_container_created": False,
                "client_container_created": False,
                "qualification_credit": 0,
                "r0_credit": 0,
                "accepted_rounds_credit": 0,
            },
            "replacement": [
                "evidence_only_bundle_specific_gate_binding_stage",
                "bind_verified_collector_and_adopted_service_sha256",
                "normalize_only_after_both_directions_and_all_streams_pass",
                "require_zero_foreign_device_io_and_counter_timeouts",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_gate_binding_bundle.json"
        ).write_bytes(canonical(invalidated_gate_binding))
        invalidated_missing_producer = {
            "schema_version":
                "mysql_xp_missing_formal_producer_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_MISSING_PRODUCER_BUNDLE_ID,
            "bundle_manifest_sha256":
                INVALIDATED_MISSING_PRODUCER_MANIFEST_SHA256,
            "reason": [
                "materializer_consumed_production_capture_schema_without_"
                "bundle_bound_producer",
                "materializer_consumed_fresh_r0_qualification_schema_without_"
                "bundle_bound_producer",
                "formal_evidence_chain_was_not_runtime_materializable",
            ],
            "observed": {
                "database_container_created": False,
                "client_container_created": False,
                "capture_credit": 0,
                "r0_credit": 0,
                "accepted_rounds_credit": 0,
            },
            "replacement": [
                "bundle_bound_evidence_only_capture_and_r0_producer",
                "live_adopted_database_client_pid_starttime_revalidation",
                "strict_AT1_AT6_worker_future_and_envelope_restore_validation",
                "180_sample_R0_counter_cadence_and_fingerprint_validation",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_missing_formal_producer_bundle.json"
        ).write_bytes(canonical(invalidated_missing_producer))
        invalidated_raw_executor_missing = {
            "schema_version": "mysql_xp_missing_raw_executor_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_RAW_EXECUTOR_MISSING_BUNDLE_ID,
            "bundle_manifest_sha256": INVALIDATED_RAW_EXECUTOR_MISSING_MANIFEST_SHA256,
            "reason": [
                "formal_evidence_producer_only_consumed_raw_capture_and_r0_documents",
                "no_bundle_bound_entrypoint_actually_ran_the_sealed_HyBench_trace_JAR",
                "no_fail_closed_180_sample_counter_and_quiescence_raw_producer",
            ],
            "replacement": [
                "bundle_bound_raw_evidence_executor",
                "classpath_bound_real_HyBench_wrapper_and_trace_JAR",
                "capture_IO_restore_and_fresh_R0_counter_fail_closed_collection",
            ],
            "capture_credit": 0,
            "r0_credit": 0,
            "accepted_rounds_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        (stage / "invalidated_missing_raw_executor_bundle.json").write_bytes(
            canonical(invalidated_raw_executor_missing)
        )
        invalidated_collector_superseded = {
            "schema_version": "mysql_xp_collector_superseded_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_COLLECTOR_SUPERSEDED_BUNDLE_ID,
            "bundle_manifest_sha256": INVALIDATED_COLLECTOR_SUPERSEDED_MANIFEST_SHA256,
            "reason": [
                "shared_exact_client_counter_collector_changed_after_MySQL8_Com_counter_coverage_failure",
                "old_collector_hash_cannot_be_mixed_with_JOB_successor_evidence",
                "raw_executor_and_producer_contracts_unchanged",
            ],
            "accepted_rounds_credit": 0,
            "r0_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        (stage / "invalidated_collector_superseded_bundle.json").write_bytes(
            canonical(invalidated_collector_superseded)
        )
        invalidated_show_status_parser = {
            "schema_version": "mysql_xp_show_status_parser_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_SHOW_STATUS_PARSER_BUNDLE_ID,
            "bundle_manifest_sha256": INVALIDATED_SHOW_STATUS_PARSER_MANIFEST_SHA256,
            "reason": [
                "shared_collector_parsed_nonrequired_MySQL8_status_rows_before_filtering",
                "afaba_not_started_beyond_physical_copy_and_has_no_valid_R0_or_accepted_round",
                "raw_executor_and_producer_contracts_unchanged",
            ],
            "accepted_rounds_credit": 0,
            "r0_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        (stage / "invalidated_show_status_parser_bundle.json").write_bytes(
            canonical(invalidated_show_status_parser)
        )
        invalidated_unbound_capture_transition = {
            "schema_version": "mysql_xp_capture_transition_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_UNBOUND_CAPTURE_TRANSITION_BUNDLE_ID,
            "bundle_manifest_sha256": (
                INVALIDATED_UNBOUND_CAPTURE_TRANSITION_MANIFEST_SHA256
            ),
            "reason": [
                "capture_transition_and_raw_executor_were_added_but_the_formal_capture_producer_did_not_require_client_cgroup_identity",
                "a_host_executed_capture_ledger_could_be_projected_without_the_adopted_client_container_binding",
            ],
            "replacement": [
                "require_capture_client_cgroup_execution_true",
                "require_capture_client_container_equal_same_live_adopted_client",
            ],
            "capture_credit": 0,
            "r0_credit": 0,
            "accepted_rounds_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        (stage / "invalidated_unbound_capture_transition_bundle.json").write_bytes(
            canonical(invalidated_unbound_capture_transition)
        )
        invalidated_a0cc_dynamic_identity = {
            "schema_version":
                "mysql_xp_dynamic_adoption_identity_invalidation_v1",
            "status": "SUPERSEDED_DO_NOT_USE_FOR_NEW_FORMAL_LAUNCH",
            "bundle_id": A0CC_DYNAMIC_ADOPTION_IDENTITY_BUNDLE_ID,
            "bundle_manifest_sha256":
                A0CC_DYNAMIC_ADOPTION_IDENTITY_MANIFEST_SHA256,
            "reason": [
                "inspect_config_sha256_included_dynamic_NetworkSettings",
                "same_live_readback_hash_could_change_without_static_config_change",
            ],
            "replacement": [
                "hash_only_declared_static_Config_HostConfig_Mounts_and_network_declaration",
                "retain_full_dynamic_NetworkSettings_as_separate_observation",
                "produce_two_snapshot_fail_closed_field_reconciliation",
            ],
            "existing_live_attempt_policy":
                "read_only_reconcile_then_main_agent_decides_no_automatic_restart",
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "superseded_a0cc_dynamic_adoption_identity.json"
        ).write_bytes(canonical(invalidated_a0cc_dynamic_identity))
        invalidated_cdd36_root_gate = {
            "schema_version":
                "mysql_xp_bootstrap_root_gate_invalidation_v1",
            "status": "SUPERSEDED_DO_NOT_USE_FOR_NEW_FORMAL_LAUNCH",
            "bundle_id": CDD36_MISSING_BOOTSTRAP_ROOT_GATE_BUNDLE_ID,
            "bundle_manifest_sha256":
                CDD36_MISSING_BOOTSTRAP_ROOT_GATE_MANIFEST_SHA256,
            "reason": [
                "prepare_copy_normalized_datadir_to_uid_999_mode_0700",
                "bootstrap_required_full_pristine_tree_readback",
                "bootstrap_lacked_explicit_effective_uid_0_preflight",
                "nonroot_rglob_could_appear_as_pristine_summary_mismatch",
            ],
            "observed": {
                "physical_copy": "PASS",
                "jdk_publication": "PASS",
                "host_tmpfs": "PASS",
                "database_container_created": False,
                "client_container_created": False,
                "formal_r0_credit": 0,
                "accepted_rounds_credit": 0,
            },
            "replacement": [
                "fail_before_authority_or_datadir_read_when_euid_is_not_0",
                "declare_bootstrap_euid_0_in_materialized_command_contract",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "superseded_cdd36_missing_bootstrap_root_gate.json"
        ).write_bytes(canonical(invalidated_cdd36_root_gate))
        invalidated_bed961_anonymous_volume = {
            "schema_version":
                "mysql_xp_client_anonymous_volume_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": BED961_CLIENT_ANONYMOUS_VOLUME_BUNDLE_ID,
            "bundle_manifest_sha256":
                BED961_CLIENT_ANONYMOUS_VOLUME_MANIFEST_SHA256,
            "reason": [
                "mysql_image_declared_var_lib_mysql_volume_was_not_explicitly_masked_in_client_create",
                "docker_created_client_anonymous_volume_on_pg_nvme2_docker_root",
                "mysql_and_postgresql_device_noninterference_was_violated",
            ],
            "observed": {
                "client_mount_destination": "/var/lib/mysql",
                "client_mount_type": "volume",
                "client_mount_source_prefix":
                    "/mnt/nvme2n1/anonymous_resource_adaptive/docker/volumes/",
                "database_pid": 11089,
                "client_pid": 11280,
                "stopped_before_workload_or_qualification": True,
                "capture_credit": 0,
                "qualification_credit": 0,
                "formal_r0_credit": 0,
                "accepted_rounds_credit": 0,
            },
            "replacement": [
                "explicit_bundle_sealed_read_only_bind_mask_for_var_lib_mysql",
                "require_exact_client_mount_destination_set",
                "reject_every_volume_mount_and_every_mnt_nvme2n1_source",
            ],
            "formal_launch_authorized": False,
            "accepted_rounds_credit": 0,
        }
        (
            stage / "invalidated_bed961_client_anonymous_volume.json"
        ).write_bytes(canonical(invalidated_bed961_anonymous_volume))
        contract_value = contract(base, runtime, hashes)
        (stage / "xp_executable_launch_contract_v3.json").write_bytes(
            canonical(contract_value)
        )
        files = relative_files(stage)
        identity = {
            "schema_version": "mysql_xp_distributed_formal10_launch_v3",
            "scenario": "hybench_xp_io",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "immutable": True,
            "offline_build_only": True,
            "remote_actions": 0,
            "base_successor_id": base.name,
            "supersedes_bundle_id": SUPERSEDED_ID,
            "invalidated_image_reference_bundle_id":
                INVALIDATED_LAUNCH.name,
            "invalidated_tokenless_bundle_id":
                INVALIDATED_TOKENLESS_LAUNCH.name,
            "invalidated_unexpiring_bundle_id":
                INVALIDATED_UNEXPIRING_LAUNCH.name,
            "invalidated_unbound_config_bundle_id":
                INVALIDATED_UNBOUND_CONFIG_LAUNCH.name,
            "invalidated_precreate_gate_bundle_id":
                INVALIDATED_PRECREATE_GATE_LAUNCH.name,
            "invalidated_port_gate_bundle_id":
                INVALIDATED_PORT_GATE_LAUNCH.name,
            "invalidated_runtime_socket_bundle_id":
                INVALIDATED_RUNTIME_SOCKET_LAUNCH.name,
            "invalidated_legacy_findmnt_bundle_id":
                INVALIDATED_LEGACY_FINDMNT_LAUNCH.name,
            "invalidated_legacy_docker_mount_bundle_id":
                INVALIDATED_LEGACY_DOCKER_MOUNT_LAUNCH.name,
            "invalidated_partition_throttle_bundle_id":
                INVALIDATED_PARTITION_THROTTLE_LAUNCH.name,
            "invalidated_unwritable_datadir_bundle_id":
                INVALIDATED_UNWRITABLE_DATADIR_LAUNCH.name,
            "invalidated_bytecode_polluted_bundle_id":
                INVALIDATED_BYTECODE_POLLUTED_LAUNCH.name,
            "invalidated_qualification_config_bundle_id":
                INVALIDATED_QUALIFICATION_CONFIG_BUNDLE_ID,
            "invalidated_materializer_collector_bundle_id":
                INVALIDATED_MATERIALIZER_COLLECTOR_BUNDLE_ID,
            "invalidated_paired_authority_bundle_id":
                INVALIDATED_PAIRED_AUTHORITY_BUNDLE_ID,
            "invalidated_gate_binding_bundle_id":
                INVALIDATED_GATE_BINDING_BUNDLE_ID,
            "invalidated_missing_formal_producer_bundle_id":
                INVALIDATED_MISSING_PRODUCER_BUNDLE_ID,
            "invalidated_missing_raw_executor_bundle_id":
                INVALIDATED_RAW_EXECUTOR_MISSING_BUNDLE_ID,
            "invalidated_collector_superseded_bundle_id":
                INVALIDATED_COLLECTOR_SUPERSEDED_BUNDLE_ID,
            "invalidated_show_status_parser_bundle_id":
                INVALIDATED_SHOW_STATUS_PARSER_BUNDLE_ID,
            "invalidated_unbound_capture_transition_bundle_id":
                INVALIDATED_UNBOUND_CAPTURE_TRANSITION_BUNDLE_ID,
            "invalidated_parent_throttle_bundle_id":
                INVALIDATED_PARENT_THROTTLE_BUNDLE_ID,
            "superseded_dynamic_adoption_identity_bundle_id":
                A0CC_DYNAMIC_ADOPTION_IDENTITY_BUNDLE_ID,
            "superseded_missing_bootstrap_root_gate_bundle_id":
                CDD36_MISSING_BOOTSTRAP_ROOT_GATE_BUNDLE_ID,
            "invalidated_client_anonymous_volume_bundle_id":
                BED961_CLIENT_ANONYMOUS_VOLUME_BUNDLE_ID,
            "invalid_development_bundle_ids": INVALID_DEVELOPMENT_BUNDLE_IDS,
            "collector_sha256": hashes[XP_COLLECTOR_RELATIVE],
            "collector_base_sha256": BASE_COLLECTOR_SHA256,
            "launch_contract_sha256": sha256(
                stage / "xp_executable_launch_contract_v3.json"
            ),
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "publication": {
                "content_addressed": True,
                "same_filesystem": True,
                "all_entries_frozen_before_visibility": True,
                "atomic_directory_rename": True,
                "non_overwriting": True,
                "concurrent_identical_publish_idempotent": True,
                "fsync_before_and_after_rename": True,
            },
            "files": dict(sorted(files.items())),
        }
        bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
        manifest = dict(identity)
        manifest["bundle_id"] = bundle_id
        payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % hashlib.sha256(payload).hexdigest(),
            encoding="ascii",
        )
        freeze(stage)
        verify_bundle(stage, bundle_id)
        fsync_tree(stage)
        final = output_root / bundle_id
        if final.exists() or final.is_symlink():
            verify_bundle(final)
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        try:
            os.rename(str(stage), str(final))
        except OSError as error:
            if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            verify_bundle(final)
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        descriptor = os.open(str(output_root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        verify_bundle(final)
        return final
    except BaseException:
        if stage.exists():
            thaw_directories(stage)
            shutil.rmtree(stage)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    bundle = publish(args.base, args.output_root)
    print(
        json.dumps(
            {
                "schema_version": "mysql_xp_formal10_launch_build_v3",
                "status": "PUBLISHED_IMMUTABLE_EXECUTABLE_CLOSURE",
                "bundle_id": bundle.name,
                "bundle": str(bundle),
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            ensure_ascii=True,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

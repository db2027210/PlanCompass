#!/usr/bin/env python3
"""Publish the deterministic four-terminal TPC-H v33 runtime/source closure."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
BASE = (
    READINESS
    / "source-v28-b4a7a8bec9b033ae280cf4ad23459ed0a4d0abd81e8e25d2da0c3c6f7412dfb8"
)
BENCHBASE = ROOT / "formal_benchbase_tpch_v31"
RUNTIME = Path("/home/anonymous/benchbase-mysql")
JAR_NAME = "benchbase-2023-SNAPSHOT-formal-binding-trace-candidate.jar"
JAR_SHA256 = "6f3fa2c886a2fb8ed8149825b40e337e835e44ebbaf6c667866d73ffed22890e"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def include(path: Path) -> bool:
    return (
        path.is_file()
        and not path.is_symlink()
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
        and "target" not in path.parts
        and not path.name.endswith((".pyc", ".pyo"))
        and path.name != "source_manifest.json"
    )


def copy(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"unsafe TPC-H v33 closure input: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def command_version(*command: str) -> str:
    completed = subprocess.run(
        list(command),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
        timeout=30,
    )
    return completed.stdout.strip()


def readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def verify(destination: Path, bundle_id: str, files: dict[str, str]) -> None:
    manifest_path = destination / "source_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("TPC-H v33 source manifest is absent")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        destination.name != f"source-v33-{bundle_id}"
        or manifest.get("bundle_id") != bundle_id
        or manifest.get("schema_version")
        != "mysql_tpch_local_deterministic_source_v33"
        or manifest.get("files") != files
    ):
        raise RuntimeError("TPC-H v33 source identity differs")
    for relative, expected in files.items():
        path = destination / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v33 source differs: {relative}")


def main() -> int:
    jar = BENCHBASE / "target" / JAR_NAME
    if sha256(jar) != JAR_SHA256:
        raise RuntimeError("TPC-H v33 deterministic BenchBase jar differs")
    build_root = Path(
        tempfile.mkdtemp(prefix=".tpch-v33-source.", dir=READINESS)
    )
    staging = build_root / "source"
    try:
        for source in sorted(BASE.rglob("*")):
            if include(source):
                copy(source, staging / source.relative_to(BASE))
        overrides = {
            "mysql_algorithm_c_local_services.py":
                ROOT / "shell/mysql_algorithm_c_local_services_v25.py",
            "shell/mysql_algorithm_c_local_services_v25.py":
                ROOT / "shell/mysql_algorithm_c_local_services_v25.py",
            "shell/manual_mysql_sgd_r0_campaign.py":
                ROOT / "shell/manual_mysql_sgd_r0_campaign.py",
        }
        for relative, source in overrides.items():
            copy(source, staging / relative)

        runtime_root = staging / "formal_benchbase_runtime"
        copy(jar, runtime_root / "target" / JAR_NAME)
        copy(
            RUNTIME / "config/mysql/sample_tpch_config.xml",
            runtime_root / "config/mysql/sample_tpch_config.xml",
        )
        copy(
            RUNTIME / "config/plugin.xml",
            runtime_root / "config/plugin.xml",
        )
        for dependency in sorted((RUNTIME / "lib").glob("*.jar")):
            copy(dependency, runtime_root / "lib" / dependency.name)
        copy(BENCHBASE / "pom.xml", runtime_root / "build/pom.xml")
        copy(
            BENCHBASE / ".mvn/wrapper/maven-wrapper.jar",
            runtime_root / "build/.mvn/wrapper/maven-wrapper.jar",
        )
        for source in sorted((BENCHBASE / "src/main").rglob("*")):
            if include(source):
                copy(
                    source,
                    runtime_root / "build/src/main"
                    / source.relative_to(BENCHBASE / "src/main"),
                )
        deterministic_test = (
            BENCHBASE
            / "src/test/java/com/oltpbenchmark/TestDeterministicPhase.java"
        )
        copy(
            deterministic_test,
            runtime_root
            / "build/src/test/java/com/oltpbenchmark/"
            "TestDeterministicPhase.java",
        )

        files = {
            str(path.relative_to(staging)): sha256(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        identity = {
            "schema_version": "mysql_tpch_local_deterministic_source_v33",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "transport": "local_no_ssh",
            "host": "compute00",
            "immutable": True,
            "runtime_contract": {
                "terminals": 4,
                "terminal_config_bound_fail_closed": True,
                "transaction_schedule_seed": 4508333,
                "phase_rng_seeded_from_randomSeed": True,
                "phase_transaction_selection_synchronized": True,
                "query_weights":
                    "4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,16,4,4,4,4",
                "benchbase_jar": (
                    "formal_benchbase_runtime/target/" + JAR_NAME
                ),
                "benchbase_jar_sha256": JAR_SHA256,
                "minimum_client_completions_fail_closed": True,
                "database_counter_timeout_fail_closed": True,
                "full_copy_prepare_timeout_seconds": 1200,
                "plugin_registry":
                    "formal_benchbase_runtime/config/plugin.xml",
                "plugin_registry_sha256": sha256(
                    RUNTIME / "config/plugin.xml"
                ),
            },
            "dependency_closure": {
                "connector":
                    "formal_benchbase_runtime/lib/"
                    "mysql-connector-java-8.0.30.jar",
                "dependency_jar_count": len(
                    list((RUNTIME / "lib").glob("*.jar"))
                ),
                "pom":
                    "formal_benchbase_runtime/build/pom.xml",
                "java_source_complete": True,
                "determinism_test_included": True,
            },
            "toolchain": {
                "java_path": str(Path("/usr/bin/java").resolve(strict=True)),
                "java_sha256": sha256(Path("/usr/bin/java").resolve(strict=True)),
                "java_version": command_version("/usr/bin/java", "-version"),
                "maven_path": str(
                    Path(shutil.which("mvn") or "").resolve(strict=True)
                ),
                "maven_version": command_version("mvn", "-version"),
                "build_command":
                    "mvn -q -Dmaven.gitcommitid.skip=true -DskipTests package",
                "test_command":
                    "mvn -q -Dmaven.gitcommitid.skip=true "
                    "-Dtest=TestDeterministicPhase test",
            },
            "files": files,
        }
        bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
        manifest = {**identity, "bundle_id": bundle_id}
        (staging / "source_manifest.json").write_bytes(canonical(manifest))
        (staging / "source_manifest.json.sha256").write_text(
            f"{sha256(staging / 'source_manifest.json')}  source_manifest.json\n",
            encoding="ascii",
        )
        destination = READINESS / f"source-v33-{bundle_id}"
        if destination.exists():
            verify(destination, bundle_id, files)
            print(
                json.dumps(
                    {
                        "status": "EXISTS_VERIFIED",
                        "bundle_id": bundle_id,
                        "directory": str(destination),
                        "file_count": len(files),
                    },
                    sort_keys=True,
                )
            )
            return 0
        publishing = READINESS / f".source-v33-{bundle_id}.{os.getpid()}"
        staging.rename(publishing)
        readonly(publishing)
        publishing.rename(destination)
        verify(destination, bundle_id, files)
        print(
            json.dumps(
                {
                    "status": "PUBLISHED_VERIFIED",
                    "bundle_id": bundle_id,
                    "directory": str(destination),
                    "file_count": len(files),
                    "dependency_jar_count":
                        identity["dependency_closure"]["dependency_jar_count"],
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        if build_root.exists():
            shutil.rmtree(build_root)


if __name__ == "__main__":
    raise SystemExit(main())

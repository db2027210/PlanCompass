#!/usr/bin/env python3
"""Build an immutable HyBench XP runtime with the native IQ1-IQ7 bound fixed.

This never edits the shared HyBench checkout or the predecessor runtime.  The
only Java semantic change is the exclusive upper bound used by XP's native AP
worker: ``getRandomint(1, 7)`` becomes ``getRandomint(1, 8)`` so all seven
interactive queries are selected uniformly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR_ID = "9794b09f4186d24508a499108ddd70157b4f9b1a4f27432f831b3fd7308e8eac"
PREDECESSOR = ROOT / "artifacts" / "mysql_xp_v8" / PREDECESSOR_ID
OUTPUT_ROOT = ROOT / "artifacts" / "mysql_xp_native_iq1_iq7_successor_v1"
OLD = "int i = rg.getRandomint(1, 7);"
NEW = "int i = rg.getRandomint(1, 8);"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def main() -> int:
    ap_source = PREDECESSOR / "source/src/main/java/com/hybench/workload/APClient.java"
    random_source = PREDECESSOR / "source/src/main/java/com/hybench/util/RandomGenerator.java"
    tp_source = PREDECESSOR / "source/src/main/java/com/hybench/workload/TPClient.java"
    pom = PREDECESSOR / "build/pom.xml"
    for path in (ap_source, random_source, tp_source, pom):
        if not path.is_file():
            raise SystemExit(f"missing predecessor input: {path}")
    text = ap_source.read_text(encoding="utf-8")
    if text.count(OLD) != 1 or NEW in text:
        raise SystemExit("predecessor XP selector does not have the one expected defect")
    if "return currentRandom().nextInt(lower, upper);" not in random_source.read_text(encoding="utf-8"):
        raise SystemExit("random upper-bound exclusivity authority changed")
    patched = text.replace(OLD, NEW)

    identity = {
        "schema_version": "mysql_xp_native_iq1_iq7_successor_v1",
        "predecessor_bundle_id": PREDECESSOR_ID,
        "predecessor_manifest_sha256": sha256(PREDECESSOR / "manifest.json"),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workload_semantics": {
            "mode": "native_runxp",
            "iq_selection": "uniform_random_IQ1_IQ7",
            "exclusive_random_range": [1, 8],
            "at_mix_percent": [35, 25, 15, 15, 7, 3],
            "xap_xtp_baseline": [8, 8],
            "xap_xtp_reduced_ratio": [1, 1],
            "forbidden_overrides": [
                "ap_query_ids", "ap_query_weights", "ap_query_sequence",
                "ap_query_sequence_limit", "AT/TP mix", "one-query XP",
            ],
        },
        "source_authority": {
            "predecessor_ap_client_sha256": sha256(ap_source),
            "patched_ap_client_sha256": hashlib.sha256(patched.encode()).hexdigest(),
            "random_generator_sha256": sha256(random_source),
            "tp_client_sha256": sha256(tp_source),
            "pom_sha256": sha256(pom),
            "only_semantic_replacement": {"from": OLD, "to": NEW, "count": 1},
        },
        "execution_authorized": False,
        "launch_performed": False,
    }
    bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
    target = OUTPUT_ROOT / bundle_id
    if target.exists():
        print(target)
        return 0

    with tempfile.TemporaryDirectory(prefix="xp-native-iq1-iq7-", dir=str(OUTPUT_ROOT.parent)) as raw:
        stage = Path(raw)
        project = stage / "project"
        shutil.copytree(PREDECESSOR / "source/src", project / "src")
        shutil.copy2(pom, project / "pom.xml")
        patched_path = project / "src/main/java/com/hybench/workload/APClient.java"
        patched_path.chmod(0o644)
        patched_path.write_text(patched, encoding="utf-8")
        subprocess.run(
            ["mvn", "-o", "-q", "-DskipTests", "package"],
            cwd=project, check=True, timeout=300,
        )
        jars = sorted((project / "target").glob("*-jar-with-dependencies.jar"))
        if len(jars) != 1:
            raise SystemExit(f"expected one fat JAR, found {jars}")
        payload = stage / bundle_id
        (payload / "source").mkdir(parents=True)
        (payload / "runtime").mkdir()
        shutil.copy2(patched_path, payload / "source/APClient.java")
        shutil.copy2(random_source, payload / "source/RandomGenerator.java")
        shutil.copy2(tp_source, payload / "source/TPClient.java")
        shutil.copy2(jars[0], payload / "runtime/HyBench-native-IQ1-IQ7.jar")
        manifest = identity | {
            "bundle_id": bundle_id,
            "runtime_jar_sha256": sha256(payload / "runtime/HyBench-native-IQ1-IQ7.jar"),
            "status": "SEALED_NOT_EXECUTION_AUTHORITY",
        }
        manifest_path = payload / "manifest.json"
        manifest_path.write_bytes(canonical(manifest))
        (payload / "manifest.json.sha256").write_text(
            f"{sha256(manifest_path)}  manifest.json\n", encoding="ascii"
        )
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        payload.rename(target)
    for path in target.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

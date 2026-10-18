#!/usr/bin/env python3
"""Seal the execution-authorized native HyBench IQ1-IQ6 XP runtime.

The workload source is the local upstream HyBench checkout.  The native XP
selector and all IQ/AT implementations are copied byte-for-byte.  Two
technical fail-closed/instrumentation changes are applied only in HyBench.java:
worker Futures are always observed, and the already-computed XP operation rate
and count are emitted as machine-readable JSON.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path("/home/anonymous/HyBench-2024-master")
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_native_iq1_iq6_successor_v1"

OLD_FUTURES = """        for(int flength=0;flength < future.size();flength++) {
            Future f = future.get(flength);
            if (f != null && !f.isCancelled() && !f.isDone()) {
                try {
                    f.get();
                } catch (InterruptedException e) {
                    e.printStackTrace();
                } catch (ExecutionException e) {
                    e.printStackTrace();
                }
            }
        }

        if(freshness != null){
"""
NEW_FUTURES = """        for(int flength=0;flength < future.size();flength++) {
            Future f = future.get(flength);
            if (f == null || f.isCancelled()) {
                throw new IllegalStateException("XP client Future is absent or cancelled");
            }
            try {
                // isDone() is not success evidence: get() must observe every
                // worker, including workers that already failed.
                f.get();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("XP client Future wait was interrupted", e);
            } catch (ExecutionException e) {
                throw new IllegalStateException("XP client failed", e.getCause());
            }
        }

        if(freshness != null){
"""
OLD_RESULT = """                logger.info("Congs~ Test is done! Bye!");
                hybench.getRes().printResult(type);
"""
NEW_RESULT = """                logger.info("Congs~ Test is done! Bye!");
                hybench.getRes().printResult(type);
                if (type == 0) {
                    Result finalResult = hybench.getRes();
                    double operationRate = finalResult.getXpqps() + finalResult.getXptps();
                    long operationCount = finalResult.getIqTotal() + finalResult.getAtTotal();
                    System.out.println("HYBENCH_METRICS_JSON={\\\"qps\\\":" + operationRate
                            + ",\\\"query_count\\\":" + operationCount + "}");
                }
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def main() -> int:
    source_root = UPSTREAM / "src"
    hybench_source = source_root / "main/java/com/hybench/HyBench.java"
    ap_source = source_root / "main/java/com/hybench/workload/APClient.java"
    tp_source = source_root / "main/java/com/hybench/workload/TPClient.java"
    result_source = source_root / "main/java/com/hybench/stats/Result.java"
    histogram_source = source_root / "main/java/com/hybench/stats/Histogram.java"
    random_source = source_root / "main/java/com/hybench/util/RandomGenerator.java"
    pom = UPSTREAM / "pom.xml"
    required = (
        hybench_source, ap_source, tp_source, result_source, histogram_source,
        random_source, pom,
    )
    for path in required:
        if not path.is_file():
            raise SystemExit(f"missing upstream input: {path}")

    ap_text = ap_source.read_text(encoding="utf-8")
    if ap_text.count("int i = rg.getRandomint(1, 7);") != 1:
        raise SystemExit("native exclusive IQ1-IQ6 selector differs")
    if "execIQ7" in ap_text:
        raise SystemExit("upstream unexpectedly defines IQ7")
    for query_id in range(1, 7):
        if f"execIQ{query_id}(Connection conn)" not in ap_text:
            raise SystemExit(f"upstream lacks IQ{query_id}")
    if "new SynchronizedDescriptiveStatistics[6]" not in histogram_source.read_text(encoding="utf-8"):
        raise SystemExit("native six-IQ histogram differs")

    hybench_text = hybench_source.read_text(encoding="utf-8")
    if hybench_text.count(OLD_FUTURES) != 1 or hybench_text.count(OLD_RESULT) != 1:
        raise SystemExit("upstream technical patch anchors differ")
    patched_hybench = hybench_text.replace(OLD_FUTURES, NEW_FUTURES).replace(
        OLD_RESULT, NEW_RESULT
    )

    source_hashes = {
        str(path.relative_to(UPSTREAM)): sha256(path) for path in required
    }
    identity = {
        "schema_version": "mysql_xp_native_iq1_iq6_successor_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority": "user_authorized_native_hybench_2026-08-09",
        "workload_semantics": {
            "mode": "native_runxp",
            "iq_selection": "uniform_random_IQ1_IQ6",
            "exclusive_random_range": [1, 7],
            "at_mix_percent": [35, 25, 15, 15, 7, 3],
            "xap_xtp_baseline": [8, 8],
            "xap_xtp_reduced_ratio": [1, 1],
            "forbidden_overrides": [
                "ap_query_ids", "ap_query_weights", "ap_query_sequence",
                "ap_query_sequence_limit", "AT/TP mix", "one-query XP",
            ],
        },
        "source_authority": {
            "upstream_root": str(UPSTREAM),
            "upstream_source_sha256": source_hashes,
            "ap_client_preserved_byte_for_byte": True,
            "tp_client_preserved_byte_for_byte": True,
            "only_technical_changes": [
                "observe_every_XP_worker_Future_with_get",
                "emit_existing_XP_rate_and_operation_count_as_JSON",
            ],
            "patched_hybench_sha256": hashlib.sha256(patched_hybench.encode()).hexdigest(),
        },
        "execution_authorized": True,
        "launch_performed": False,
    }
    bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
    target = OUTPUT_ROOT / bundle_id
    if target.exists():
        print(target)
        return 0

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xp-native-iq1-iq6-", dir=str(OUTPUT_ROOT)) as raw:
        stage = Path(raw)
        project = stage / "project"
        shutil.copytree(source_root, project / "src")
        shutil.copy2(pom, project / "pom.xml")
        patched_path = project / "src/main/java/com/hybench/HyBench.java"
        patched_path.write_text(patched_hybench, encoding="utf-8")
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
        shutil.copy2(patched_path, payload / "source/HyBench.java")
        for path in (ap_source, tp_source, result_source, histogram_source, random_source):
            shutil.copy2(path, payload / "source" / path.name)
        jar = payload / "runtime/HyBench-native-IQ1-IQ6.jar"
        shutil.copy2(jars[0], jar)
        manifest = identity | {
            "bundle_id": bundle_id,
            "runtime_jar_sha256": sha256(jar),
            "status": "EXECUTION_AUTHORIZED",
        }
        manifest_path = payload / "manifest.json"
        manifest_path.write_bytes(canonical(manifest))
        (payload / "manifest.json.sha256").write_text(
            f"{sha256(manifest_path)}  manifest.json\n", encoding="ascii"
        )
        payload.rename(target)

    for path in target.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

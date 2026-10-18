#!/usr/bin/env python3
"""Build executable native XP IQ1-IQ7 by completing the missing IQ7 path.

The predecessor already samples ``execIQ1`` through ``execIQ7`` uniformly but
only implements IQ1-IQ6.  IQ7 uses HyBench's existing AP-7 SQL and the exact
native AP-7 parameter generator, while recording the operation in the XP IQ
histogram/counter.  AT1-AT6 and all client ratios are unchanged.
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
PREDECESSOR = ROOT / "artifacts/mysql_xp_v8" / PREDECESSOR_ID
FAILED_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v7_retry2_20260809T195600p0800/r0_binding.json"
)
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_native_iq1_iq7_successor_v2"
OLD_SELECTOR = "int i = rg.getRandomint(1, 7);"
NEW_SELECTOR = "int i = rg.getRandomint(1, 8);"
METHOD_ANCHOR = "    // 12 AP Queries\n"
IQ7_METHOD = """    public ClientResult execIQ7(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int companyid;
        long responseTime = 0L;
        try {
            // Native IQ7 is the existing HyBench AP-7 statement with the
            // existing AP-7 company-id parameter generator.
            pstmt = conn.prepareStatement(sqls.ap_q7());
            companyid = rg.getRandomint(customer_no,customer_no+company_no);
            pstmt.setInt(1,companyid);
            pstmt.setInt(2,companyid);
            pstmt.setInt(3,companyid);
            pstmt.setInt(4,companyid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getXPIQItem(6).addValue(responseTime);
            lock.lock();
            iqTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        } finally {
            cr.setRt(responseTime);
            if (pstmt != null) {
                try {
                    pstmt.close();
                } catch (SQLException e) {
                    e.printStackTrace();
                }
            }
        }
        return cr;
    }

"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def main() -> int:
    source_root = PREDECESSOR / "source/src"
    ap_path = source_root / "main/java/com/hybench/workload/APClient.java"
    histogram_path = source_root / "main/java/com/hybench/stats/Histogram.java"
    result_path = source_root / "main/java/com/hybench/stats/Result.java"
    hybench_path = source_root / "main/java/com/hybench/HyBench.java"
    pom = PREDECESSOR / "build/pom.xml"
    failed = json.loads(FAILED_R0.read_text(encoding="utf-8"))
    for path in (ap_path, histogram_path, result_path, hybench_path, pom):
        if not path.is_file():
            raise SystemExit(f"missing predecessor input: {path}")
    if (
        failed.get("status") != "R0_FAILED_CLOSED"
        or len(failed.get("failures") or []) != 4
        or not all("missing workload method" in row.get("error", "")
                   for row in failed["failures"])
        or any((failed.get("accepted_rounds") or {}).values())
    ):
        raise SystemExit("IQ7 failed-R0 authority differs")
    ap = ap_path.read_text(encoding="utf-8")
    histogram = histogram_path.read_text(encoding="utf-8")
    result = result_path.read_text(encoding="utf-8")
    hybench = hybench_path.read_text(encoding="utf-8")
    if (
        ap.count(OLD_SELECTOR) != 1
        or NEW_SELECTOR in ap
        or "execIQ7(Connection conn)" in ap
        or ap.count(METHOD_ANCHOR) != 1
        or ap.count("public ClientResult execQ7(Connection conn)") != 1
        or histogram.count("xpstat_iq = new SynchronizedDescriptiveStatistics[6]") != 1
        or histogram.count("for(int i = 0;i<6;i++){\n            xpstat_iq[i]") != 1
        or result.count("for (int xpidx = 0; xpidx < 6; xpidx++)") != 1
        or "HYBENCH_METRICS_JSON" not in hybench
        or "Future.isDone() is not success evidence" not in hybench
    ):
        raise SystemExit("IQ7 patch anchors differ")
    patched_ap = ap.replace(OLD_SELECTOR, NEW_SELECTOR).replace(
        METHOD_ANCHOR, IQ7_METHOD + METHOD_ANCHOR
    )
    patched_histogram = histogram.replace(
        "xpstat_iq = new SynchronizedDescriptiveStatistics[6]",
        "xpstat_iq = new SynchronizedDescriptiveStatistics[7]",
    ).replace(
        "for(int i = 0;i<6;i++){\n            xpstat_iq[i]",
        "for(int i = 0;i<7;i++){\n            xpstat_iq[i]",
    )
    patched_result = result.replace(
        "for (int xpidx = 0; xpidx < 6; xpidx++)",
        "for (int xpidx = 0; xpidx < 7; xpidx++)",
    )
    identity = {
        "schema_version": "mysql_xp_native_iq1_iq7_successor_v2",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority": "user_supplied_AGENTS_2026-08-09",
        "predecessor_bundle_id": PREDECESSOR_ID,
        "predecessor_manifest_sha256": sha256(PREDECESSOR / "manifest.json"),
        "failed_r0_path": str(FAILED_R0.relative_to(ROOT)),
        "failed_r0_sha256": sha256(FAILED_R0),
        "workload_semantics": {
            "mode": "native_runxp",
            "iq_selection": "uniform_random_IQ1_IQ7",
            "exclusive_random_range": [1, 8],
            "iq7_sql_authority": "existing_HyBench_AP_7",
            "iq7_parameter_authority": "existing_HyBench_execQ7",
            "at_mix_percent": [35, 25, 15, 15, 7, 3],
            "xap_xtp_baseline": [8, 8],
            "xap_xtp_reduced_ratio": [1, 1],
            "forbidden_overrides": [
                "ap_query_ids", "ap_query_weights", "ap_query_sequence",
                "ap_query_sequence_limit", "AT/TP mix", "one-query XP",
            ],
        },
        "source_authority": {
            "predecessor_ap_client_sha256": sha256(ap_path),
            "patched_ap_client_sha256": hashlib.sha256(patched_ap.encode()).hexdigest(),
            "predecessor_histogram_sha256": sha256(histogram_path),
            "patched_histogram_sha256": hashlib.sha256(patched_histogram.encode()).hexdigest(),
            "predecessor_result_sha256": sha256(result_path),
            "patched_result_sha256": hashlib.sha256(patched_result.encode()).hexdigest(),
            "hybench_technical_runtime_sha256": sha256(hybench_path),
            "changes": [
                "selector_exclusive_upper_bound_7_to_8",
                "implement_execIQ7_using_existing_AP7_SQL_and_parameters",
                "expand_XP_IQ_histogram_6_to_7",
                "expand_XP_IQ_result_output_6_to_7",
            ],
            "at_and_tp_sources_unchanged": True,
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
    with tempfile.TemporaryDirectory(prefix="xp-native-iq1-iq7-v2-", dir=str(OUTPUT_ROOT)) as raw:
        stage = Path(raw)
        project = stage / "project"
        shutil.copytree(source_root, project / "src")
        shutil.copy2(pom, project / "pom.xml")
        patched_ap_path = project / "src/main/java/com/hybench/workload/APClient.java"
        patched_histogram_path = project / "src/main/java/com/hybench/stats/Histogram.java"
        patched_result_path = project / "src/main/java/com/hybench/stats/Result.java"
        for path in (patched_ap_path, patched_histogram_path, patched_result_path):
            path.chmod(0o644)
        patched_ap_path.write_text(patched_ap, encoding="utf-8")
        patched_histogram_path.write_text(patched_histogram, encoding="utf-8")
        patched_result_path.write_text(patched_result, encoding="utf-8")
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
        shutil.copy2(patched_ap_path, payload / "source/APClient.java")
        shutil.copy2(patched_histogram_path, payload / "source/Histogram.java")
        shutil.copy2(patched_result_path, payload / "source/Result.java")
        shutil.copy2(project / "src/main/java/com/hybench/HyBench.java", payload / "source/HyBench.java")
        jar = payload / "runtime/HyBench-native-IQ1-IQ7-v2.jar"
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

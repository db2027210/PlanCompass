#!/usr/bin/env python3
"""Build an immutable HyBench XP release without AP-selection extensions."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


FORBIDDEN = (
    "ap_query_ids",
    "ap_query_weights",
    "ap_query_sequence",
    "ap_query_sequence_limit",
)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matching_brace(text: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"unmatched opening brace at byte {opening}")


def native_ap_client(source: str) -> str:
    helper_start = source.index("    private int[] parseApIntList")
    iq_marker = source.index("    // 6 Interative Queries", helper_start)
    source = source[:helper_start] + source[iq_marker:]

    branch_marker = "            else if(type == 7){"
    branch_start = source.index(branch_marker)
    opening = source.index("{", branch_start)
    branch_end = matching_brace(source, opening) + 1
    native_branch = """            else if(type == 7){
                int round = 1;
                while(!exitFlag){
                    long roundElapsedTime = 0L;
                    ArrayList<Integer> runList = getRandomList(1,13);
                    StringBuilder sb = new StringBuilder("Current thread " + Thread.currentThread().getName()
                            + " - Run List in current round " + round + " : ");
                    for(int idx:runList){
                        sb.append("Q").append(idx).append(" ");
                    }
                    logger.info(sb.toString());
                    for (int i = 0; i < 13; i++) {
                        Method method = apClass.getMethod("execQ" + runList.get(i), Connection.class);
                        ClientResult cr = (ClientResult) method.invoke(this, conn);
                        totalElapsedTime += cr.getRt();
                        roundElapsedTime += cr.getRt();
                    }
                    if (exitFlag) {
                        break;
                    }
                    logger.info("Current thread " + Thread.currentThread().getName() + " - round " + round
                            + " elapsed time is " + String.format("%d",roundElapsedTime) + "(ms)");
                    round++;
                }
                ret.setApRound(round);
                ret.setRt(totalElapsedTime);
            }"""
    result = source[:branch_start] + native_branch + source[branch_end:]
    result = result.replace(
        """    public int Get_blocked_transfer_Id(){
        int Id=0;
        Id=Related_Blocked_Transfer_ids.get(rg.getRandomint(Related_Blocked_Transfer_ids.size()));
        return Id;
    }""",
        """    public int Get_blocked_transfer_Id(){
        if (Related_Blocked_Transfer_ids == null || Related_Blocked_Transfer_ids.isEmpty()) {
            return rg.getRandomint(customer_no);
        }
        return Related_Blocked_Transfer_ids.get(rg.getRandomint(Related_Blocked_Transfer_ids.size()));
    }""",
    )
    result = result.replace(
        "if(queue_ids.size()==0)",
        "if(queue_ids == null || queue_ids.size()==0)",
    )
    present = [key for key in FORBIDDEN if key in result]
    if present:
        raise ValueError(f"forbidden AP-selection tokens remain: {present}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="/home/anonymous/HyBench-2024-master")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"immutable output already exists: {output}")
    shutil.copytree(source, output, ignore=shutil.ignore_patterns("target", "hybench-error.log", "*.backup"))
    ap_client = output / "src/main/java/com/hybench/workload/APClient.java"
    ap_client.write_text(native_ap_client(ap_client.read_text(encoding="utf-8")), encoding="utf-8")
    subprocess.run(["mvn", "-q", "-DskipTests", "package"], cwd=output, check=True)
    jar = output / "target/HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar"
    runtime_jar = output / "lib/HyBench-1.0-SNAPSHOT.jar"
    shutil.copy2(jar, runtime_jar)
    manifest = {
        "contract": "hybench_native_xp_no_ap_selection_v1",
        "source": str(source),
        "output": str(output),
        "ap_client_sha256": sha256_path(ap_client),
        "statement_sha256": sha256_path(output / "conf/stmt_postgres.toml"),
        "jar": str(jar),
        "jar_sha256": sha256_path(jar),
        "forbidden_tokens_absent": True,
    }
    (output / "native_release_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

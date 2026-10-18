-- v83 mechanism-2: Incremental Sort + ordered GroupAggregate
-- Default: HashAggregate + full Sort (large work_mem-resident tables).
-- Candidate (high cpu_tuple / operator cost): Parallel Index Scan on sourceid
--   → Incremental Sort (presorted sourceid) → GroupAggregate.
-- Distinct from closed IndexOnly-only rename: requires Incremental Sort node
-- and multi-key (sourceid, type) with only sourceid index order.
SELECT sourceid,
       type,
       count(*)::bigint AS n,
       sum(amount) AS s
FROM transfer
WHERE sourceid BETWEEN 1 AND 800000
  AND amount > 0
GROUP BY sourceid, type
ORDER BY sourceid, type;

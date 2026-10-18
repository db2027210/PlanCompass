-- v83 mechanism-2b: Incremental Sort without Aggregate (pure sort-memory role)
-- Default may full-sort or use Incremental Sort under cheap index costs.
-- Multi-key ORDER BY (sourceid, type, targetid) with index only on sourceid.
SELECT sourceid, type, targetid, amount
FROM transfer
WHERE sourceid BETWEEN 1 AND 600000
  AND amount > 0
ORDER BY sourceid, type, targetid;

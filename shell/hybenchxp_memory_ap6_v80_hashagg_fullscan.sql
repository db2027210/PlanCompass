-- v80 multi-batch HashAgg construction: fullscan transfer group by sourceid
SELECT count(*) FROM (
  SELECT t.sourceid, count(*) n, sum(t.amount) amt, max(t.targetid) mx, min(t.type) mn
  FROM transfer t WHERE t.sourceid BETWEEN 1 AND 5000000 AND t.amount > 0
  GROUP BY t.sourceid
) bonect_memory_v80_hashagg_fullscan;

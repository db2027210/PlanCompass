-- v80 multi-batch HashJoin+HashAgg hybrid: transfer ⋈ savingaccount + group
SELECT count(*) FROM (
  SELECT t.sourceid, count(*) n, sum(t.amount) amt, max(s.balance) bal
  FROM transfer t JOIN savingaccount s ON s.accountid = t.sourceid
  WHERE t.sourceid BETWEEN 1 AND 2000000 AND t.amount > 0
  GROUP BY t.sourceid
) bonect_memory_v80_join_hashagg;

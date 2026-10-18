-- v82 mechanism-2b: wide Parallel Hash build (multi-column build payload)
-- Forces a fatter Parallel Hash table (balance+userid+flags) under fixed work_mem
-- so Batches>1 appears with enable_parallel_hash=on (not parhash=off).
SELECT count(*), sum(s.balance), sum(t.amount), sum(s.userid::bigint)
FROM savingaccount s
JOIN transfer t ON t.sourceid = s.accountid
WHERE t.sourceid BETWEEN 1 AND 3500000
  AND t.amount > 0
  AND s.balance IS NOT NULL;

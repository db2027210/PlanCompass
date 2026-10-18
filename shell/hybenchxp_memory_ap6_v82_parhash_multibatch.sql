-- v82 mechanism-2: true multi-batch Parallel Hash (enable_parallel_hash=on)
-- Wider/larger build than SA: checkingaccount ⋈ transfer under low fixed work_mem
-- produces Parallel Hash with Batches>1 while parallel hash remains enabled.
SELECT count(*), sum(c.balance), sum(t.amount)
FROM checkingaccount c
JOIN transfer t ON t.sourceid = c.accountid
WHERE t.sourceid BETWEEN 1 AND 4000000
  AND t.amount > 0;

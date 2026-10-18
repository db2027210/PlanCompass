-- v79 Join+Agg hybrid: HashJoin then HashAgg on join keys (memory-resident stages).
SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS n, sum(t.amount) AS amt, max(s.balance) AS bal
    FROM transfer t
    JOIN savingaccount s ON s.accountid = t.sourceid
    WHERE t.sourceid BETWEEN 1 AND 1200000
      AND t.amount > 0
    GROUP BY t.sourceid
) bonect_memory_v79_join_transfer_sa_agg;

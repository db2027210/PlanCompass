-- v86 M01: WindowAgg plus a large partition/order Sort over realistic transfer rows.
-- Pair-fixed GUCs are supplied by the v86 Phase A probe.
SELECT count(*),
       sum(running_amount),
       sum(partition_count::numeric)
FROM (
    SELECT t.sourceid,
           sum(t.amount) OVER (
               PARTITION BY t.sourceid % 10000
               ORDER BY t.amount, t.targetid
           ) AS running_amount,
           count(*) OVER (PARTITION BY t.sourceid % 10000) AS partition_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 1800000
      AND t.amount > 0
) AS bonect_memory_v86_m01;

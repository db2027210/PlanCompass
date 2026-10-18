-- v75 Memory fair AP-6: plan-sensitive family A (low cost threshold) — same shape as v73.
-- Default: Parallel Seq Scan + HashAggregate (high memory).
-- Intermediate/high seq_page_cost (>=~1.2): Parallel Index Only + Sorted/GroupAgg.
-- Keep full 2.9M range so default stays Seq+HashAgg.
SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2900000
    GROUP BY t.sourceid
) bonect_memory_v75_ap6_indexonly_switch;

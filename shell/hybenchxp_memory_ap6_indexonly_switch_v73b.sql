-- v73 Memory fair AP-6: plan-sensitive family A (low cost threshold).
-- Default: Parallel Seq Scan + HashAggregate (high memory).
-- Intermediate/high seq_page_cost (>=~1.2): Parallel Index Only + Sorted/GroupAgg.
-- Keep full 2.9M range so default stays Seq+HashAgg (smaller ranges force IndexOnly even at default).
SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2900000
    GROUP BY t.sourceid
) bonect_memory_v73_ap6_indexonly_switch;

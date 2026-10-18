-- v72 Memory fair AP-6: keep v70 IndexOnly switch family.
-- Default (low seq_page_cost): Parallel Seq Scan + HashAggregate (high memory).
-- High seq_page_cost / c06-style costs: Parallel Index Only Scan + Group/Sorted agg (lower memory, faster).
-- Range unchanged from v70/v71 to preserve proven plan boundary.
SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2900000
    GROUP BY t.sourceid
) bonect_memory_v72_ap6_indexonly_switch;

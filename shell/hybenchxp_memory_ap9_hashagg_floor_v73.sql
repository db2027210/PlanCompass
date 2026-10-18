-- v73 Memory fair AP-9: plan-sensitive family B + memory floor (HIGH cost threshold).
-- Default..spc~3: Bitmap Heap + HashAggregate (retains memory when AP-6 already flipped).
-- spc>=5 / c06-class: Index Scan + Sorted agg (second stage; do NOT use as first intermediate).
-- Narrow sourceid band + ORDER/LIMIT keeps hashagg path distinct from AP-6 full-range IndexOnly.
SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count, sum(t.amount) AS total_amount
    FROM transfer t
    WHERE t.sourceid BETWEEN 1200001 AND 1500000
    GROUP BY t.sourceid
    ORDER BY total_amount DESC, row_count DESC
    LIMIT 300000
) bonect_memory_v73_ap9_hashagg_floor;

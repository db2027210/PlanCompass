-- v78: additional real group key `type` (7 categories) → higher HashAgg cardinality.
-- Forces non-IndexOnly (idx_transfer_1 covers sourceid only).
SELECT count(*)
FROM (
    SELECT t.sourceid, t.type, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
    GROUP BY t.sourceid, t.type
) bonect_memory_v78_src_type;

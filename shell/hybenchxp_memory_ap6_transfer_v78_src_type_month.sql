-- v78: sourceid + type + month(timestamp) — higher cardinality, realistic keys.
SELECT count(*)
FROM (
    SELECT
        t.sourceid,
        t.type,
        date_trunc('month', t.timestamp) AS mon,
        count(*) AS row_count,
        sum(t.amount) AS total_amount
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
    GROUP BY t.sourceid, t.type, date_trunc('month', t.timestamp)
) bonect_memory_v78_src_type_month;

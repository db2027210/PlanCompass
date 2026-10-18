SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count, sum(t.amount) AS total_amount
    FROM transfer t
    WHERE t.sourceid BETWEEN 2400001 AND 2700000
    GROUP BY t.sourceid
    ORDER BY total_amount DESC, row_count DESC
    LIMIT 300000
) bonect_memory_hashagg;

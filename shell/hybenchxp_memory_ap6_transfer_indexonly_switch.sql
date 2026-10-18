SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 1500000
    GROUP BY t.sourceid
    ORDER BY row_count DESC
    LIMIT 1500000
) bonect_memory_indexonly_switch;

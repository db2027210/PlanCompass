SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2800000
    GROUP BY t.sourceid
) bonect_memory_groupagg_switch;

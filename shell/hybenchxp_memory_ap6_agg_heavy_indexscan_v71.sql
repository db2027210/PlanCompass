SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2900000
    GROUP BY t.sourceid
) bonect_memory_v71_groupagg_switch;

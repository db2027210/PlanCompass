SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
      AND t.ctid < '(400000,0)'
    GROUP BY t.sourceid
) bonect_memory_groupagg_switch;

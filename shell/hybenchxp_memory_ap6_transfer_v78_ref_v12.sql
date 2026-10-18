-- v78 control: identical to v12 fullscan (sourceid groups, count only)
SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
    GROUP BY t.sourceid
) bonect_memory_v78_ref;

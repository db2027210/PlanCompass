SET work_mem = '512MB';

SELECT count(*)
FROM (
    SELECT ch.sourceid,
           count(*) AS row_count,
           sum(ch.amount) AS total_amount
    FROM checking ch
    WHERE ch.sourceid BETWEEN 1 AND 1500000
    GROUP BY ch.sourceid
    ORDER BY total_amount DESC, row_count DESC
    LIMIT 200000
) bonect_memory_workset;

RESET work_mem;

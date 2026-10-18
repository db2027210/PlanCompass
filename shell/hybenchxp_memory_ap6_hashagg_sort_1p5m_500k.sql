SELECT count(*)
FROM (
    SELECT ch.sourceid,
           ch.targetid,
           ch.type,
           count(*) AS row_count,
           sum(ch.amount) AS total_amount
    FROM checking ch
    WHERE ch.sourceid BETWEEN 1 AND 1500000
    GROUP BY ch.sourceid, ch.targetid, ch.type
    ORDER BY total_amount DESC, row_count DESC
    LIMIT 500000
) bonect_memory_hashagg;

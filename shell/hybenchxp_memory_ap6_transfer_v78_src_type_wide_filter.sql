-- v78: wide sourceid+type groups with light HAVING to keep groups realistic.
SELECT count(*)
FROM (
    SELECT
        t.sourceid,
        t.type,
        count(*) AS row_count,
        sum(t.amount) AS total_amount,
        avg(t.amount) AS avg_amount,
        min(t.amount) AS min_amount,
        max(t.amount) AS max_amount
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
    GROUP BY t.sourceid, t.type
    HAVING count(*) >= 1
) bonect_memory_v78_src_type_wide_filter;

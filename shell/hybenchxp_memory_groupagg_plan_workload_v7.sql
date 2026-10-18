SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2800000
    GROUP BY t.sourceid
) bonect_memory_groupagg_switch;

SELECT sum(metric)
FROM (
    SELECT sum(lt.amount)::double precision AS metric
    FROM loantrans lt
    UNION ALL
    SELECT sum(sa.balance)::double precision AS metric
    FROM savingaccount sa
) bonect_memory_fixed_heap_workset;

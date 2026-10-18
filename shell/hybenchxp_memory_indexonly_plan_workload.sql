SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS row_count
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 1500000
    GROUP BY t.sourceid
    ORDER BY row_count DESC
    LIMIT 1500000
) bonect_memory_indexonly_switch;

SELECT sum(metric)
FROM (
    SELECT sum(lt.amount)::double precision AS metric
    FROM loantrans lt
    UNION ALL
    SELECT sum(la.amount)::double precision AS metric
    FROM loanapps la
    UNION ALL
    SELECT sum(c.age)::double precision AS metric
    FROM customer c
) bonect_memory_fixed_heap_workset;

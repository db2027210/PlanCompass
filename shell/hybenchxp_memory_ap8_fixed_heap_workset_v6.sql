SELECT sum(metric)
FROM (
    SELECT sum(lt.amount)::double precision AS metric
    FROM loantrans lt
    UNION ALL
    SELECT sum(c.age)::double precision AS metric
    FROM customer c
) bonect_memory_fixed_heap_workset;

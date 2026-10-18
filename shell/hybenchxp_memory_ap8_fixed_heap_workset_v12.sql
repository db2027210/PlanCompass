SELECT sum(metric)
FROM (
    SELECT sum(lt.amount)::double precision AS metric
    FROM loantrans lt
    UNION ALL
    SELECT sum(sa.balance)::double precision AS metric
    FROM savingaccount sa
    UNION ALL
    SELECT sum(c.age)::double precision AS metric
    FROM customer c TABLESAMPLE SYSTEM (55) REPEATABLE (20260711)
) bonect_memory_fixed_heap_workset;

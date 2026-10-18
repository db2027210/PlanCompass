SELECT sum(metric)
FROM (
    SELECT sum(lt.amount)::double precision AS metric
    FROM loantrans lt
    UNION ALL
    SELECT sum(sa.balance)::double precision AS metric
    FROM savingaccount sa
    UNION ALL
    SELECT sum(ch.amount)::double precision AS metric
    FROM checking ch
    UNION ALL
    SELECT sum(c.age)::double precision AS metric
    FROM customer c TABLESAMPLE SYSTEM (50) REPEATABLE (20260714)
) bonect_memory_v71_fixed_heap;

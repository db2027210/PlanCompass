-- v73 Memory fair AP-8: low-IO fixed heap residual floor (plan-near-invariant).
-- Same family as v72 light: loantrans + saving + customer 25% sample; NO checking full scan.
SELECT sum(metric)
FROM (
    SELECT sum(lt.amount)::double precision AS metric
    FROM loantrans lt
    UNION ALL
    SELECT sum(sa.balance)::double precision AS metric
    FROM savingaccount sa
    UNION ALL
    SELECT sum(c.age)::double precision AS metric
    FROM customer c TABLESAMPLE SYSTEM (30) REPEATABLE (20260714)
) bonect_memory_v73_fixed_heap_light;

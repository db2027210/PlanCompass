-- v72 Memory fair AP-8: light fixed heap floor WITHOUT checking full scan.
-- v71 added full checking + customer 50% and with xap=2 drove IO to ~157 MiB/s (purity FAIL).
-- v72 removes checking; uses loantrans + saving + modest customer sample only
-- (same family as pure v70 AP-8 / v11, plan-invariant fixed workset, low steady-state IO).
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
) bonect_memory_v72_fixed_heap_light;

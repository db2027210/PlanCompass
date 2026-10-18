-- v75g reverse-Memory switch: Sort/Materialize heavy window over transfer.
-- Default often Materialize/Sort under low cpu costs; candidate with higher
-- cpu_operator_cost / cpu_tuple_cost may choose streaming aggregate paths.
SELECT count(*)
FROM (
    SELECT t.sourceid,
           sum(t.amount) OVER (PARTITION BY t.sourceid % 10000 ORDER BY t.amount) AS run_amt,
           count(*) OVER (PARTITION BY t.sourceid % 10000) AS cnt
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 800000
) bonect_memory_v75g_ap6_reverse_sort_material;

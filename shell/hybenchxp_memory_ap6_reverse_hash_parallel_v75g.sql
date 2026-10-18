-- v75g reverse-Memory switch candidate (AP-6).
-- Intent: default prefers parallel multi-worker HashAggregate (high working set);
-- candidate with high parallel_setup_cost / parallel_tuple_cost collapses Gather
-- so only one HashAgg runs (lower concurrent hash tables), without flipping to
-- Index Only Scan (which empirically raised Memory on AP6 spc2).
-- Group key is an expression so Index Only Sorted path is unavailable.
SELECT count(*)
FROM (
    SELECT (t.sourceid % 500000) AS g,
           count(*) AS c,
           sum(t.amount) AS a
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2200000
    GROUP BY 1
) bonect_memory_v75g_ap6_reverse_hash_parallel;

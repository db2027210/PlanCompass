-- v75g high-cardinality sustained floor: ~6M-group HashAgg on expression keys (no IndexOnly path).
-- EXPLAIN w1: HashAggregate > Seq Scan:transfer identical at spc=1 and spc=2.
-- Must pass floor-only 2min A/B (avg WS) before composite use.
SELECT count(*)
FROM (
    SELECT (t.sourceid % 800000) AS g1,
           ((t.amount::numeric) / 50)::bigint AS g2,
           count(*) AS c,
           sum(t.amount) AS a
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2500000
    GROUP BY 1, 2
) bonect_memory_v75_floor_transfer_highcard_hash;

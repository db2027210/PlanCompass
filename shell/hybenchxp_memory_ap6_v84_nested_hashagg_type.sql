-- v84 mechanism-1 variant: nested double HashAgg with type-aware outer rollup.
-- Inner: GROUP BY (sourceid, type) high-card HashAgg.
-- Outer: rollup by type (second HashAgg over Subquery).
-- Confirmed default stock costs: HashAggregate → HashAggregate → Gather…
SELECT x.type,
       count(*)::bigint AS n_keys,
       sum(x.n)::bigint AS n_rows,
       sum(x.s) AS total_amt
FROM (
    SELECT t.sourceid,
           t.type,
           count(*)::bigint AS n,
           sum(t.amount) AS s
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2000000
      AND t.amount > 0
    GROUP BY t.sourceid, t.type
) x
GROUP BY x.type;

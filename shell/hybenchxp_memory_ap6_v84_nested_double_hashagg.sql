-- v84 mechanism-1: nested_double_hashagg_collapse (primary hierarchical shape)
-- INNER high-card GROUP BY transfer.sourceid (large HashAgg lifetime) +
-- OUTER HashAgg over subquery rollup key (second HashAgg lifetime).
-- Distinct from v82 dual same-level Partial / GROUPING SETS; v78 width/key;
-- v83 IncSort+GroupAgg. Default under stock costs: two HashAggregate nodes.
-- Candidate: remove ≥1 large Hash level under one seven-vector with joint
-- speed≥15% and mag≥328 MiB (must measure; not claimed a priori).
SELECT ((x.sourceid * 2654435761) % 500000) AS rollup_key,
       count(*)::bigint AS n_keys,
       sum(x.n)::bigint AS n_rows,
       sum(x.s) AS total_amt
FROM (
    SELECT t.sourceid,
           count(*)::bigint AS n,
           sum(t.amount) AS s
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2500000
      AND t.amount > 0
    GROUP BY t.sourceid
) x
GROUP BY ((x.sourceid * 2654435761) % 500000);

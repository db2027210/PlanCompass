-- v82 mechanism-1: parallel partial-aggregate multiplicity
-- Two independent GROUP BYs (sourceid / targetid) joined so default plan
-- holds two Partial HashAggregate trees (each with parallel workers).
SELECT count(*), sum(a.n)::bigint, sum(b.n)::bigint
FROM (
    SELECT t.sourceid, count(*)::bigint AS n, sum(t.amount) AS s
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2500000
      AND t.amount > 0
    GROUP BY t.sourceid
) a
JOIN (
    SELECT t.targetid AS sourceid, count(*)::bigint AS n, sum(t.amount) AS s
    FROM transfer t
    WHERE t.targetid BETWEEN 1 AND 2500000
      AND t.amount > 0
    GROUP BY t.targetid
) b USING (sourceid);

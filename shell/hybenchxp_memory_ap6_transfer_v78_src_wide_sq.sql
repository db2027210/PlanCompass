-- v78: sourceid groups with wider state including sum(amount*amount).
SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS c, sum(t.amount) AS s, sum(t.amount * t.amount) AS s2,
           avg(t.amount) AS a, min(t.amount) AS mn, max(t.amount) AS mx, stddev_pop(t.amount) AS sd
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
    GROUP BY t.sourceid
) bonect_memory_v78_src_wide_sq;

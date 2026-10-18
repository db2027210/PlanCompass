-- v78: sourceid + (sourceid%2) integer secondary key; keeps parallel HashAgg default.
SELECT count(*)
FROM (
    SELECT t.sourceid, (t.sourceid % 2) AS k, count(*) AS c,
           sum(t.amount) AS s, avg(t.amount) AS a, min(t.amount) AS mn, max(t.amount) AS mx
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
    GROUP BY t.sourceid, (t.sourceid % 2)
) bonect_memory_v78_src_mod2;

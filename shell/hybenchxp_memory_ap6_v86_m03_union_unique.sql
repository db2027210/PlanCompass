-- v86 M03: UNION deduplication, distinct from closed INTERSECT/EXCEPT SetOp probes.
-- Wide projection prevents an IndexOnly-only speed path from qualifying.
SELECT count(*), sum(sourceid::numeric)
FROM (
    SELECT t.sourceid, t.targetid, t.amount, t.type
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 1800000
      AND t.type IN ('transfer', 'invest')
      AND t.amount > 0
    UNION
    SELECT t.sourceid, t.targetid, t.amount, t.type
    FROM transfer t
    WHERE t.targetid BETWEEN 1 AND 1800000
      AND t.type IN ('transfer', 'donate')
      AND t.amount > 0
) AS bonect_memory_v86_m03;

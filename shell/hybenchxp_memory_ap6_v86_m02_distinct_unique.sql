-- v86 M02: wide DISTINCT starts from HashAggregate when favorable and may
-- move to Sort+Unique under the same fixed non-cost GUCs.
SELECT count(*), sum(sourceid::numeric)
FROM (
    SELECT DISTINCT t.sourceid, t.targetid, t.amount, t.type
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 1800000
      AND t.amount > 0
) AS bonect_memory_v86_m02;

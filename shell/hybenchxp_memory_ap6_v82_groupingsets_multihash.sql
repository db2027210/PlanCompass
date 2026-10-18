-- v82 mechanism-1b: multi-hash-key HashAggregate via GROUPING SETS
-- Default plan uses one HashAggregate node with multiple concurrent Hash Keys
-- over a parallel Gather of transfer rows (multiple live hash tables).
SELECT count(*)
FROM (
    SELECT t.sourceid, t.type, count(*) AS n, sum(t.amount) AS s
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 3000000
      AND t.amount > 0
    GROUP BY GROUPING SETS ((t.sourceid), (t.sourceid, t.type), (t.type))
) bonect_memory_v82_groupingsets;

-- v75 Memory fair AP-10: second low-threshold plan-sensitive family (DB lever, lower mem than AP6).
-- Expected: default Parallel Seq+HashAgg; spc>=~1.2 IndexOnly+Sorted (same flip class as AP6).
-- Used to gain DB without raising AP6 weight above 45%.
SELECT count(*)
FROM (
    SELECT t.targetid, count(*) AS row_count
    FROM transfer t
    WHERE t.targetid BETWEEN 1 AND 2500000
    GROUP BY t.targetid
) bonect_memory_v75_ap10_transfer_target_flip;

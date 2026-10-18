-- v74 Memory fair AP-9: high-memory HashAgg floor that does NOT flip at spc=2/3.
-- EXPLAIN (w1 57433): Aggregate:Hashed > Seq Scan:savingaccount holds through spc=1..75
--   with ORDER BY bal + LIMIT (defeats IndexOnly Sorted path used by plain group).
-- Microprobe: second-highest working-set peak after AP6 full HashAgg; much heavier than v73 AP9 r3.
-- Design: carry baseline Memory floor; only AP6 low-threshold family releases at intermediate candidates.
SELECT count(*)
FROM (
    SELECT sa.accountid, sum(sa.balance) AS bal, count(*) AS cnt
    FROM savingaccount sa
    GROUP BY sa.accountid
    ORDER BY bal DESC
    LIMIT 500000
) bonect_memory_v74_ap9_saving_hash_sort_floor;

-- v75 floor-only: savingaccount HashAgg + Sort + Limit (v74 AP-9 floor, no flip ≤75).
-- Plan: Limit > Sort > Aggregate:Hashed > Seq Scan:savingaccount
-- Microprobe peak ~86% was single-sample; sustained avg must be re-proven here.
SELECT count(*)
FROM (
    SELECT sa.accountid, sum(sa.balance) AS bal, count(*) AS cnt
    FROM savingaccount sa
    GROUP BY sa.accountid
    ORDER BY bal DESC
    LIMIT 500000
) bonect_memory_v75_floor_saving_hash_sort;

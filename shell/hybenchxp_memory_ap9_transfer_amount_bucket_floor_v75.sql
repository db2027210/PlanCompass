-- v75 Memory fair AP-9: sustained HashAgg floor (passed floor-only 2min A/B).
-- Floor A/B (w1 20260714_090608): default Mem 79.7508% / spc2 79.7018% (Δ0.049pp);
-- structure identical; samples>=96; physical blkio; series post-warmup p50~79.7 p90~80.3.
-- Plan holds Aggregate:Hashed through spc=2/3/75.
-- Plan: Aggregate:Sorted > Gather Merge > Sort > Aggregate:Hashed > Seq Scan:transfer
-- first_flip_spc=75; holds Hashed at spc=2 and spc=3.
SELECT count(*)
FROM (
    SELECT (t.amount::numeric / 10)::bigint AS bucket, count(*) AS cnt, sum(t.amount) AS amt
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2000000
    GROUP BY (t.amount::numeric / 10)::bigint
) bonect_memory_v75_floor_transfer_amount_bucket;
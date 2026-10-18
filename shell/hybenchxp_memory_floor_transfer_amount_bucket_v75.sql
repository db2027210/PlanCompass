-- v75 floor-only: transfer amount-bucket HashAgg (stable plan through high spc).
-- Plan: Aggregate:Sorted > Gather Merge > Sort > Aggregate:Hashed > Seq Scan:transfer
-- first_flip_spc=75; holds Hashed at spc=2 and spc=3.
SELECT count(*)
FROM (
    SELECT (t.amount::numeric / 10)::bigint AS bucket, count(*) AS cnt, sum(t.amount) AS amt
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2000000
    GROUP BY (t.amount::numeric / 10)::bigint
) bonect_memory_v75_floor_transfer_amount_bucket;

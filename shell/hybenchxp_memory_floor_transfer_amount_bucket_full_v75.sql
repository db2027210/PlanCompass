-- v75c heavier sustained floor: full transfer amount-bucket HashAgg (no range filter).
-- Must pass floor-only 2min A/B before composite use. Plan class same as transfer_amount_bucket.
SELECT count(*)
FROM (
    SELECT (t.amount::numeric / 10)::bigint AS bucket, count(*) AS cnt, sum(t.amount) AS amt
    FROM transfer t
    GROUP BY (t.amount::numeric / 10)::bigint
) bonect_memory_v75_floor_transfer_amount_bucket_full;

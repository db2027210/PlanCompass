-- v78: multi-stage AP mix — hold large HashAgg (per sourceid,type) then small rollup.
-- Still one transfer scan; extends resident group-state lifetime without second concurrent fullscan.
WITH per_key AS (
    SELECT
        t.sourceid,
        t.type,
        count(*) AS row_count,
        sum(t.amount) AS total_amount,
        avg(t.amount) AS avg_amount,
        min(t.amount) AS min_amount,
        max(t.amount) AS max_amount
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
    GROUP BY t.sourceid, t.type
),
rollup AS (
    SELECT
        type,
        count(*) AS n_keys,
        sum(row_count) AS n_rows,
        sum(total_amount) AS total_amount
    FROM per_key
    GROUP BY type
)
SELECT sum(n_keys)::bigint AS n_keys, sum(n_rows)::bigint AS n_rows, sum(total_amount) AS total_amount
FROM rollup;

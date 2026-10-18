-- v78: same group key as v12, wider aggregate state only (amount stats).
-- amount columns block pure IndexOnly count path.
SELECT count(*)
FROM (
    SELECT
        t.sourceid,
        count(*) AS row_count,
        sum(t.amount) AS total_amount,
        avg(t.amount) AS avg_amount,
        min(t.amount) AS min_amount,
        max(t.amount) AS max_amount,
        stddev_pop(t.amount) AS std_amount
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
    GROUP BY t.sourceid
) bonect_memory_v78_src_wide;

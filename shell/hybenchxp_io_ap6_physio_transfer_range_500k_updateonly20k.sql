WITH slot AS (
    SELECT (pg_backend_pid() % 10)::bigint AS value
),
updated AS (
    UPDATE bonect_hybench_physio_probe AS p
    SET payload = repeat(md5((clock_timestamp()::text || ':' || p.id::text)), 64),
        updated_at = clock_timestamp()
    FROM slot
    WHERE p.id BETWEEN slot.value * 20000 + 1
                   AND slot.value * 20000 + 20000
    RETURNING p.id
)
SELECT count(*) AS updated_rows
FROM updated;

SELECT count(*) AS transfer_count,
       sum(amount) AS transfer_amount
FROM transfer
WHERE id BETWEEN 1 AND 500000;

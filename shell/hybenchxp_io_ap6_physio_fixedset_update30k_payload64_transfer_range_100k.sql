WITH update_window AS (
    SELECT ((pg_backend_pid() % 8) * 30000 + 1) AS lo
)
UPDATE bonect_hybench_physio_probe AS p
SET payload = repeat(md5(p.id::text || clock_timestamp()::text), 64),
    updated_at = clock_timestamp()
FROM update_window AS w
WHERE p.id BETWEEN w.lo AND w.lo + 29999;

SELECT count(*) AS transfer_count,
       sum(amount) AS transfer_amount
FROM transfer
WHERE id BETWEEN 1 AND 100000;

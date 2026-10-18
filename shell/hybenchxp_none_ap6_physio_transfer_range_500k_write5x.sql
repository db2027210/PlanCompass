INSERT INTO bonect_hybench_physio_probe(bucket, payload)
SELECT (random() * 1000)::int, repeat(md5(random()::text), 32)
FROM generate_series(1, 3000);

UPDATE bonect_hybench_physio_probe
SET payload = repeat(md5(random()::text), 32),
    updated_at = clock_timestamp()
WHERE id IN (
    SELECT id
    FROM bonect_hybench_physio_probe
    WHERE (id % 3) = (pg_backend_pid() % 3)
    ORDER BY random()
    LIMIT 1500
);

SELECT count(*) AS transfer_count,
       sum(amount) AS transfer_amount
FROM transfer
WHERE id BETWEEN 1 AND 500000;

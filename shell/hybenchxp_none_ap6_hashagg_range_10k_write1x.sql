INSERT INTO bonect_hybench_physio_probe(bucket, payload)
SELECT (random() * 1000)::int, repeat(md5(random()::text), 32)
FROM generate_series(1, 600);

UPDATE bonect_hybench_physio_probe
SET payload = repeat(md5(random()::text), 32),
    updated_at = clock_timestamp()
WHERE id IN (
    SELECT id
    FROM bonect_hybench_physio_probe
    WHERE (id % 3) = (pg_backend_pid() % 3)
    ORDER BY random()
    LIMIT 300
);

SELECT sourceid,
       targetid,
       sum(amount) AS total_amount
FROM transfer
WHERE sourceid BETWEEN 1 AND 10000
   OR targetid BETWEEN 1 AND 10000
GROUP BY sourceid, targetid
ORDER BY total_amount DESC
LIMIT 20;

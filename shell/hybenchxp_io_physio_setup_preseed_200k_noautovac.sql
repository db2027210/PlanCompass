CREATE TABLE IF NOT EXISTS bonect_hybench_physio_probe (
    id bigserial PRIMARY KEY,
    bucket int NOT NULL DEFAULT 0,
    payload text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE bonect_hybench_physio_probe
    ALTER COLUMN payload SET STORAGE EXTERNAL;

ALTER TABLE bonect_hybench_physio_probe
    SET (autovacuum_enabled = false, toast.autovacuum_enabled = false);

TRUNCATE bonect_hybench_physio_probe;

INSERT INTO bonect_hybench_physio_probe(id, bucket, payload)
SELECT g,
       g % 64,
       repeat(md5(g::text), 64)
FROM generate_series(1, 200000) AS g;

SELECT setval(
    pg_get_serial_sequence('bonect_hybench_physio_probe', 'id'),
    200000,
    true
);

ANALYZE bonect_hybench_physio_probe;

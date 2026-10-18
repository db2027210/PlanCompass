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

TRUNCATE bonect_hybench_physio_probe RESTART IDENTITY;

INSERT INTO bonect_hybench_physio_probe(bucket, payload)
SELECT (g % 2048)::int, repeat(md5(g::text), 64)
FROM generate_series(1, 288000) AS g;

ANALYZE bonect_hybench_physio_probe;

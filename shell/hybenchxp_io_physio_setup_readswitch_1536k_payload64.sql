CREATE TABLE IF NOT EXISTS bonect_hybench_io_read_probe (
    id bigserial PRIMARY KEY,
    bucket int NOT NULL,
    transfer_id bigint NOT NULL,
    payload text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE bonect_hybench_io_read_probe
    ALTER COLUMN payload SET STORAGE EXTERNAL;

ALTER TABLE bonect_hybench_io_read_probe
    SET (autovacuum_enabled = false, toast.autovacuum_enabled = false);

TRUNCATE bonect_hybench_io_read_probe RESTART IDENTITY;

INSERT INTO bonect_hybench_io_read_probe(bucket, transfer_id, payload)
SELECT (g % 4096)::int,
       ((g % 1500000) + 1)::bigint,
       repeat(md5(g::text), 64)
FROM generate_series(1, 1536000) AS g;

CREATE INDEX IF NOT EXISTS bonect_hybench_io_read_probe_bucket_idx
    ON bonect_hybench_io_read_probe(bucket);

CREATE INDEX IF NOT EXISTS bonect_hybench_io_read_probe_transfer_idx
    ON bonect_hybench_io_read_probe(transfer_id);

ANALYZE bonect_hybench_io_read_probe;

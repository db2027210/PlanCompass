CREATE TABLE IF NOT EXISTS bonect_hybench_physio_probe (
    id bigserial PRIMARY KEY,
    bucket int NOT NULL DEFAULT 0,
    payload text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE bonect_hybench_physio_probe
    ALTER COLUMN payload SET STORAGE EXTERNAL;

TRUNCATE bonect_hybench_physio_probe;

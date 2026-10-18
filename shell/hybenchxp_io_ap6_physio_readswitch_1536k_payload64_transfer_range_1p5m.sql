WITH scan_window AS MATERIALIZED (
    SELECT ((pg_backend_pid() % 16) * 128)::int AS lo
),
freshen AS (
    UPDATE bonect_hybench_io_read_probe AS p
    SET updated_at = clock_timestamp()
    FROM scan_window AS w
    WHERE p.bucket BETWEEN w.lo AND w.lo + 7
      AND (p.id % 64) = (pg_backend_pid() % 64)
    RETURNING p.id
),
probe_rows AS MATERIALIZED (
    SELECT p.bucket,
           p.transfer_id,
           length(p.payload) AS payload_len
    FROM bonect_hybench_io_read_probe AS p
    CROSS JOIN scan_window AS w
    WHERE p.bucket BETWEEN w.lo AND w.lo + 511
),
transfer_rows AS MATERIALIZED (
    SELECT t.id,
           t.amount
    FROM transfer AS t
    WHERE t.id BETWEEN 1 AND 1500000
)
SELECT (p.bucket / 16) AS bucket_group,
       count(*) AS joined_rows,
       sum(t.amount) AS total_amount,
       sum(p.payload_len) AS payload_bytes_seen,
       (SELECT count(*) FROM freshen) AS freshened_rows
FROM probe_rows AS p
JOIN transfer_rows AS t
  ON t.id = p.transfer_id
GROUP BY bucket_group
ORDER BY total_amount DESC
LIMIT 64;

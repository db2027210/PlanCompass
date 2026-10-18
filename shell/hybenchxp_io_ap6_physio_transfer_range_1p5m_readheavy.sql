SELECT count(*) AS transfer_count,
       sum(amount) AS transfer_amount,
       avg(amount) AS transfer_avg
FROM transfer
WHERE id BETWEEN 1 AND 1500000;

SELECT bucket,
       count(*) AS probe_rows,
       max(length(payload)) AS max_payload_len
FROM bonect_hybench_physio_probe
WHERE bucket BETWEEN 0 AND 1000
GROUP BY bucket
ORDER BY bucket;

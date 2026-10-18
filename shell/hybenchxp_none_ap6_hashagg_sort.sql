SELECT sourceid,
       count(*) AS transfer_count,
       sum(amount) AS transfer_amount
FROM transfer
WHERE amount >= 0
GROUP BY sourceid
ORDER BY transfer_amount DESC
LIMIT 20;

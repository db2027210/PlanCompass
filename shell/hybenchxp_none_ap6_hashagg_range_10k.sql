SELECT sourceid,
       targetid,
       sum(amount) AS total_amount
FROM transfer
WHERE sourceid BETWEEN 1 AND 10000
   OR targetid BETWEEN 1 AND 10000
GROUP BY sourceid, targetid
ORDER BY total_amount DESC
LIMIT 20;

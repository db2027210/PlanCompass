SELECT cu.companyid,
       count(*) AS transfer_cnt,
       sum(t.amount) AS total_amount
FROM customer cu
JOIN transfer t ON t.sourceid = cu.custid
GROUP BY cu.companyid
ORDER BY total_amount DESC
LIMIT 100;

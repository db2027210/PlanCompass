SELECT cu.companyid, count(*) AS checking_cnt, sum(ch.amount) AS total_amount
FROM customer cu
JOIN checking ch ON ch.sourceid = cu.custid
GROUP BY cu.companyid
ORDER BY total_amount DESC
LIMIT 100;

SELECT cu.companyid,
       count(*) AS checking_cnt,
       sum(ch.amount) AS total_amount
FROM customer cu
JOIN checking ch ON ch.sourceid = cu.custid
WHERE ch.sourceid BETWEEN 1 AND 2000000
GROUP BY cu.companyid
ORDER BY total_amount DESC
LIMIT 100;

-- v79 AP-6 variant C: amount>0 full-join filter (predicate-bounded; still checking/company/group shape)
SELECT cu.companyid,
       count(*) AS checking_cnt,
       sum(ch.amount) AS total_amount
FROM customer cu
JOIN checking ch ON ch.sourceid = cu.custid
WHERE ch.amount > 0
GROUP BY cu.companyid
ORDER BY total_amount DESC
LIMIT 100;

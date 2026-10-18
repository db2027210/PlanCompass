SELECT cu.companyid,
       count(*) AS cnt,
       avg(sa.balance) AS avg_saving,
       avg(ca.balance) AS avg_checking
FROM customer cu
JOIN savingaccount sa ON sa.userid = cu.custid
JOIN checkingaccount ca ON ca.userid = cu.custid
WHERE sa.balance > 1000
  AND ca.balance > 1000
GROUP BY cu.companyid
ORDER BY cnt DESC
LIMIT 100;

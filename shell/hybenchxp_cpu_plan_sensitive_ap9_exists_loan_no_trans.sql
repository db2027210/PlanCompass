SELECT cu.companyid, count(*) AS cnt
FROM customer cu
WHERE EXISTS (
  SELECT 1
  FROM loanapps la
  WHERE la.applicantid = cu.custid
    AND la.amount > 1000
)
AND NOT EXISTS (
  SELECT 1
  FROM loantrans lt
  WHERE lt.applicantid = cu.custid
    AND lt.status = 'accept'
)
GROUP BY cu.companyid
ORDER BY cnt DESC
LIMIT 100;

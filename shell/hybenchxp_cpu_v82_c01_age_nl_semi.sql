-- v82 C01 secondary: selective age-filtered NL Semi baseline from the v80 lesson.
SELECT cu.companyid, count(*) AS cnt
FROM customer cu
WHERE cu.age BETWEEN 35 AND 45
  AND EXISTS (
    SELECT 1
    FROM loanapps la
    WHERE la.applicantid = cu.custid
      AND la.amount > 20000
  )
GROUP BY cu.companyid
ORDER BY cnt DESC, cu.companyid
LIMIT 100;

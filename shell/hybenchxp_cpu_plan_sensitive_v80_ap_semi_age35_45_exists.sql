-- v80 Phase A CPU-role probe: age-filtered EXISTS (loanapps)
-- Default plan class: Nested Loop Semi Join + Index Scan on loanapps
-- citc-high candidate class: Parallel Hash Semi Join (structure flip OK;
-- ANALYZE elapsed ratio never met ≤0.85 admission on probe w1).
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
ORDER BY cnt DESC
LIMIT 100;

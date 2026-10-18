-- v80 Phase A alternate EXISTS shape (age 30-40, amount>5000)
-- Default: Nested Loop Semi; citc-high: Parallel Hash Semi
-- Speed gate failed (cand slower due to large hash build / temp batches).
SELECT cu.companyid, count(*) AS cnt
FROM customer cu
WHERE cu.age BETWEEN 30 AND 40
  AND EXISTS (
    SELECT 1
    FROM loanapps la
    WHERE la.applicantid = cu.custid
      AND la.amount > 5000
  )
GROUP BY cu.companyid
ORDER BY cnt DESC
LIMIT 100;

-- v83 mechanism-1b: low-cardinality Memoize on company (20k keys) via customer.
-- Default Parallel Hash Join builds company (tiny). Seven-cost vectors may
-- select Nested Loop + Memoize(companyid) with high hit ratio.
SELECT count(*)::bigint AS n,
       sum(co.loan_balance) AS sum_loan,
       sum(cu.custid::bigint) AS sum_cust
FROM customer cu
JOIN company co ON co.companyid = cu.companyid
WHERE cu.custid BETWEEN 1 AND 2800000;

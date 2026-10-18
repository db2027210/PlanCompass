-- v83 mechanism-1: low-cardinality parameterized Nested Loop + Memoize
-- loanapps.applicantid has ~35k distinct over 6M rows (high reuse, not 2M unique sourceid).
-- Default often Nested Loop + Memoize(customer); high citc may switch to Hash Join build.
SELECT count(*)::bigint AS n,
       sum(c.custid::bigint) AS sum_cust,
       sum(l.amount) AS sum_amt
FROM loanapps l
JOIN customer c ON c.custid = l.applicantid
WHERE l.id BETWEEN 1 AND 3000000
  AND l.amount > 0;

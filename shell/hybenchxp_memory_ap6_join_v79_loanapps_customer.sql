-- v79 Join class: loanapps ⋈ customer on applicantid=custid (second fact×entity).
SELECT count(*)
FROM (
    SELECT l.applicantid, l.amount, l.duration, c.province, c.loan_balance, c.isblocked
    FROM loanapps l
    JOIN customer c ON c.custid = l.applicantid
    WHERE l.applicantid BETWEEN 1 AND 1500000
      AND l.amount > 0
) bonect_memory_v79_join_loanapps_customer;

-- v79 Join class: transfer ⋈ customer on sourceid=custid (entity dimension).
SELECT count(*)
FROM (
    SELECT t.sourceid, t.amount, c.province, c.city, c.loan_balance, c.isblocked
    FROM transfer t
    JOIN customer c ON c.custid = t.sourceid
    WHERE t.sourceid BETWEEN 1 AND 1000000
      AND t.amount > 0
) bonect_memory_v79_join_transfer_customer;

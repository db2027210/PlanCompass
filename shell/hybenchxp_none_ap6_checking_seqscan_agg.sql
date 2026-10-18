SELECT count(*) AS checking_count,
       sum(amount) AS checking_amount
FROM checking
WHERE amount >= 0;

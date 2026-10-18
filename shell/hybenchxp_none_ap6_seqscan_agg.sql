SELECT count(*) AS transfer_count,
       sum(amount) AS transfer_amount
FROM transfer
WHERE amount >= 0;

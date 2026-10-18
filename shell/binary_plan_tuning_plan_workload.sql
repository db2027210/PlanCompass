SELECT count(*)
FROM bpt_orders
WHERE account_id BETWEEN 1000 AND 1600;

SELECT sum(amount)
FROM bpt_orders
WHERE status = 2
  AND created_at BETWEEN DATE '2025-03-01' AND DATE '2025-04-15';

SELECT o.account_id, o.amount, o.created_at
FROM bpt_orders o
WHERE o.amount > 975
ORDER BY o.amount DESC
LIMIT 100;

SELECT a.region, count(*), sum(o.amount)
FROM bpt_accounts a
JOIN bpt_orders o ON o.account_id = a.id
WHERE a.region BETWEEN 3 AND 12
GROUP BY a.region
ORDER BY sum(o.amount) DESC;

SELECT a.status, o.status, count(*)
FROM bpt_accounts a
JOIN bpt_orders o ON o.account_id = a.id
WHERE o.created_at >= DATE '2025-06-01'
GROUP BY a.status, o.status
ORDER BY count(*) DESC;

SELECT count(*)
FROM bpt_orders o
WHERE EXISTS (
    SELECT 1
    FROM bpt_accounts a
    WHERE a.id = o.account_id
      AND a.status = 3
);

SELECT status, avg(amount), percentile_disc(0.95) WITHIN GROUP (ORDER BY amount)
FROM bpt_orders
GROUP BY status
ORDER BY status;

SELECT count(*)
FROM bpt_orders
WHERE payload LIKE 'a%';

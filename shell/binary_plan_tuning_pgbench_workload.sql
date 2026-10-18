\set aid random(1, 100000)
\set aid2 random(1, 100000)
\set st random(0, 5)
\set reg random(0, 19)
\set cutoff random(50, 1050)
SELECT count(*) FROM bpt_orders WHERE account_id BETWEEN :aid AND :aid + 600;
SELECT sum(amount) FROM bpt_orders WHERE status = :st AND created_at BETWEEN DATE '2025-03-01' AND DATE '2025-07-31';
SELECT o.account_id, o.amount FROM bpt_orders o WHERE o.amount > :cutoff ORDER BY o.amount DESC LIMIT 50;
SELECT a.region, count(*), sum(o.amount) FROM bpt_accounts a JOIN bpt_orders o ON o.account_id = a.id WHERE a.region = :reg GROUP BY a.region;
SELECT count(*) FROM bpt_orders o WHERE EXISTS (SELECT 1 FROM bpt_accounts a WHERE a.id = o.account_id AND a.id BETWEEN :aid2 AND :aid2 + 1000);
UPDATE bpt_touch SET v = v + 1 WHERE id = :aid;

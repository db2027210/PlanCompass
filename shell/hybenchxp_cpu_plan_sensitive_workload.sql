-- Plan-sensitive HyBenchXP CPU workload probe.
-- Concrete SQL for EXPLAIN-based plan/resource screening. HyBench runtime uses
-- stmt_postgres.toml plus statement_sql_files overrides from the CPU config.

SELECT co.companyid,
       count(CASE WHEN lt.status = 'accept' THEN 1 ELSE NULL END) * 100.0 / count(la.id) AS rate
FROM company co, loanapps la, loantrans lt
WHERE co.category = 'telecommunication'
  AND co.companyid = la.applicantid
  AND la.applicantid = lt.applicantid
GROUP BY co.companyid
ORDER BY rate DESC
LIMIT 10;

SELECT co.companyid,
       count(CASE WHEN lt.status = 'accept' THEN 1 ELSE NULL END) * 100.0 / count(la.id) AS rate
FROM company co, loanapps la, loantrans lt
WHERE co.category = 'software_IT'
  AND co.companyid = la.applicantid
  AND la.applicantid = lt.applicantid
GROUP BY co.companyid
ORDER BY rate DESC
LIMIT 10;

SELECT c.name, sum(t.amount) AS total_revenue
FROM company c, transfer t
WHERE c.companyid = t.targetid
  AND EXISTS (
    SELECT 1
    FROM loanapps la
    WHERE la.applicantid = c.companyid
      AND date(timestamp::timestamp) BETWEEN '2023-11-01' AND (CAST('2023-11-01' AS date) + 60)
  )
  AND NOT EXISTS (
    SELECT 1
    FROM loantrans lt
    WHERE lt.applicantid = c.companyid
      AND date(timestamp::timestamp) BETWEEN '2023-11-01' AND (CAST('2023-11-01' AS date) + 60)
  )
GROUP BY c.name
ORDER BY total_revenue DESC;

SELECT cu.companyid, count(*) AS checking_cnt, sum(ch.amount) AS total_amount
FROM customer cu
JOIN checking ch ON ch.sourceid = cu.custid
GROUP BY cu.companyid
ORDER BY total_amount DESC
LIMIT 100;

SELECT cu.companyid,
       count(*) AS cnt,
       avg(sa.balance) AS avg_saving,
       avg(ca.balance) AS avg_checking
FROM customer cu
JOIN savingaccount sa ON sa.userid = cu.custid
JOIN checkingaccount ca ON ca.userid = cu.custid
WHERE sa.balance > 1000
  AND ca.balance > 1000
GROUP BY cu.companyid
ORDER BY cnt DESC
LIMIT 100;

SELECT cu.companyid, count(*) AS cnt
FROM customer cu
WHERE EXISTS (
  SELECT 1
  FROM loanapps la
  WHERE la.applicantid = cu.custid
    AND la.amount > 1000
)
AND NOT EXISTS (
  SELECT 1
  FROM loantrans lt
  WHERE lt.applicantid = cu.custid
    AND lt.status = 'accept'
)
GROUP BY cu.companyid
ORDER BY cnt DESC
LIMIT 100;

WITH big_account_inbound AS (
  SELECT t.targetid AS id, c.name AS name, sum(t.amount) AS amount
  FROM customer c, transfer t
  WHERE date(t.timestamp::timestamp) BETWEEN '2023-11-01' AND (CAST('2023-11-01' AS date) + 60)
    AND c.custid = t.targetid
  GROUP BY name, id
  ORDER BY amount DESC
  LIMIT 10
),
big_account_outbound AS (
  SELECT b.id, b.name AS name, sum(t.amount) AS amount
  FROM big_account_inbound b, transfer t
  WHERE date(t.timestamp::timestamp) BETWEEN '2023-11-01' AND (CAST('2023-11-01' AS date) + 60)
    AND b.id = t.sourceid
  GROUP BY b.id, b.name
)
SELECT bin.id, bin.name, (sum(bin.amount) - sum(bout.amount)) AS revenue
FROM big_account_inbound bin, big_account_outbound bout
WHERE bin.id = bout.id
GROUP BY bin.id, bin.name
ORDER BY revenue DESC;

SELECT co.companyid,
       cu.custid,
       sum(t.amount) AS invest_amount,
       count(t.amount) AS count,
       avg(sa.balance) AS saving_balance,
       avg(ca.balance) AS checking_balance,
       avg(la.amount) AS avg_loan_amount
FROM company co, transfer t, customer cu, savingaccount sa, loanapps la, checkingaccount ca
WHERE t.targetid = co.companyid
  AND t.sourceid = cu.custid
  AND cu.custid = sa.userid
  AND cu.custid = ca.userid
  AND la.applicantid = cu.custid
  AND t.type = 'invest'
  AND sa.balance > 1000
  AND ca.balance > 1000
  AND co.category IN (
    'software_IT',
    'internet_service',
    'telecommunication',
    'technology_service',
    'computer_communication_manufacturing'
  )
GROUP BY co.companyid, cu.custid
ORDER BY invest_amount DESC,
         count DESC,
         saving_balance DESC,
         checking_balance DESC,
         avg_loan_amount DESC;

SELECT t.targetid, sum(t.amount) AS total_amount
FROM transfer t
GROUP BY t.targetid
ORDER BY total_amount DESC
LIMIT 100;

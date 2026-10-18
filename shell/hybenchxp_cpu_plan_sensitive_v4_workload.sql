-- Expanded plan-sensitive HyBenchXP CPU workload probe.
-- AP-6/8/9/10/11 are no-parameter sections in HyBench, so these overrides
-- avoid Java prepared-statement parameter binding mismatches.
-- Q12 stays excluded because v1/v2 showed shared-memory resize failures.

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

SELECT cu.companyid,
       count(*) AS transfer_cnt,
       sum(t.amount) AS total_amount
FROM customer cu
JOIN transfer t ON t.sourceid = cu.custid
GROUP BY cu.companyid
ORDER BY total_amount DESC
LIMIT 100;

SELECT co.category, count(*) AS cnt, sum(t.amount) AS total_amount
FROM company co
JOIN transfer t ON t.targetid = co.companyid
WHERE co.category IN (
  'software_IT',
  'internet_service',
  'telecommunication',
  'agriculture'
)
GROUP BY co.category
ORDER BY total_amount DESC;

SELECT tr.sourceid AS sourceid, tr.targetid AS targetid, tr.type AS type, sum(tr.amount) AS amount
FROM company co, transfer tr, customer cu
WHERE cu.custid = tr.sourceid
  AND tr.targetid = co.companyid
  AND co.companyid = 3000033
  AND type != 'salary'
GROUP BY sourceid, targetid, type
UNION ALL
SELECT ch.sourceid AS sourceid, ch.targetid AS targetid, ch.type AS type, sum(ch.amount) AS amount
FROM company co, checking ch, customer cu
WHERE cu.custid = ch.sourceid
  AND ch.targetid = co.companyid
  AND co.companyid = 3000033
  AND type != 'salary'
GROUP BY sourceid, targetid, type
UNION ALL
SELECT tr.sourceid AS sourceid, tr.targetid AS targetid, tr.type AS type, sum(tr.amount) AS amount
FROM company co, transfer tr, customer cu
WHERE cu.custid = tr.targetid
  AND tr.sourceid = co.companyid
  AND co.companyid = 3000033
  AND type != 'salary'
GROUP BY sourceid, targetid, type
UNION ALL
SELECT ch.sourceid AS sourceid, ch.targetid AS targetid, ch.type AS type, sum(ch.amount) AS amount
FROM company co, checking ch, customer cu
WHERE cu.custid = ch.targetid
  AND ch.sourceid = co.companyid
  AND co.companyid = 3000033
  AND type != 'salary'
GROUP BY sourceid, targetid, type
ORDER BY amount DESC
LIMIT 10;

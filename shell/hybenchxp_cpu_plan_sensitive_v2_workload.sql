-- Lighter plan-sensitive HyBenchXP CPU workload probe.
-- Uses original HyBench AP shapes with concrete values for EXPLAIN screening.

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

SELECT custid, name, phone, sum(amount)
FROM (
  SELECT cu.custid, cu.name, cu.phone, t.amount
  FROM customer cu
  JOIN transfer t ON cu.custid = t.targetid
  WHERE custid = 33757
  UNION ALL
  SELECT cu.custid, cu.name, cu.phone, ch.amount
  FROM customer cu
  JOIN checking ch ON cu.custid = ch.targetid
  WHERE cu.custid = 33757
) income
GROUP BY custid, name, phone;

SELECT custid, name, phone, sum(amount)
FROM (
  SELECT cu.custid, cu.name, cu.phone, t.amount
  FROM customer cu
  JOIN transfer t ON cu.custid = t.sourceid
  WHERE custid = 33757
  UNION ALL
  SELECT cu.custid, cu.name, cu.phone, ch.amount
  FROM customer cu
  JOIN checking ch ON cu.custid = ch.sourceid
  WHERE cu.custid = 33757
) outcome
GROUP BY custid, name, phone;

SELECT cu.custid, la.timestamp, la.amount, lt.timestamp, lt.amount, lt.status
FROM customer cu, loanapps la, loantrans lt
WHERE cu.custid = la.applicantid
  AND la.id = lt.appid
  AND cu.custid = 33757;

SELECT cu.custid, sum(tr.amount) AS total_amount
FROM company co, transfer tr, customer cu
WHERE cu.custid = tr.targetid
  AND cu.companyid = co.companyid
  AND tr.sourceid = co.companyid
  AND co.companyid = 3000033
  AND tr.type = 'salary'
GROUP BY cu.custid
ORDER BY total_amount DESC;

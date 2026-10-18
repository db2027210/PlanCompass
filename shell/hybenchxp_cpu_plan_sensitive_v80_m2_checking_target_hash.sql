-- v80 Phase A Mechanism-2 co/CPU probe: checking⋈customer(targetid) aggregate
-- Default: Finalize/Partial HashAggregate + Gather + Parallel Hash Join
-- ptc≈3 candidate: serial Hash Join + HashAggregate (NOT MergeJoin / NOT NL+Bit)
-- ANALYZE: serial path ~3.3× slower → speed admission fail.
SELECT cu.companyid, count(*) AS cnt, sum(ch.amount) AS tot
FROM checking ch
JOIN customer cu ON cu.custid = ch.targetid
WHERE ch.amount > 200
  AND ch.timestamp >= TIMESTAMP '2018-01-01'
GROUP BY cu.companyid
ORDER BY tot DESC
LIMIT 100;

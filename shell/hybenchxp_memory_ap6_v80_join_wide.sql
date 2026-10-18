-- v80 multi-batch HashJoin / Memoize lifetime: wide transfer ⋈ savingaccount
SELECT count(*), sum(balance), sum(userid::bigint), sum(isblocked::int), sum(amt) FROM (
  SELECT t.sourceid, s.balance, s.userid, s.isblocked, t.amount AS amt
  FROM transfer t JOIN savingaccount s ON s.accountid = t.sourceid
  WHERE t.sourceid BETWEEN 1 AND 2000000 AND t.amount > 0
) bonect_memory_v80_join_wide;

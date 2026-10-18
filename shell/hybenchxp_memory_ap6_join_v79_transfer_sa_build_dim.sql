-- v79 Join class: force useful build on full dimension by probing a large transfer slice
-- while selecting dimension columns that widen the hash tuple.
SELECT count(*)
FROM (
    SELECT s.accountid, s.userid, s.balance, s.isblocked, t.amount, t.type
    FROM savingaccount s
    JOIN transfer t ON t.sourceid = s.accountid
    WHERE s.accountid BETWEEN 1 AND 2500000
      AND t.sourceid BETWEEN 1 AND 2500000
      AND t.amount > 10
) bonect_memory_v79_join_transfer_sa_build_dim;

-- v79 Join class: transfer ⋈ savingaccount on semantic sourceid=accountid.
-- Default should prefer Hash Join (build dimension / mid transfer slice).
-- Candidate seven-cost boundary aims Nested Loop or Merge Join without Cartesian fan-out.
SELECT count(*)
FROM (
    SELECT t.sourceid, t.amount, s.balance, s.isblocked
    FROM transfer t
    JOIN savingaccount s ON s.accountid = t.sourceid
    WHERE t.sourceid BETWEEN 1 AND 800000
      AND t.amount > 0
) bonect_memory_v79_join_transfer_sa_mid;

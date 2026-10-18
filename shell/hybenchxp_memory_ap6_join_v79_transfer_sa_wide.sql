-- v79 Join class: wider transfer ⋈ savingaccount slice for larger Hash build/probe.
SELECT count(*)
FROM (
    SELECT t.sourceid, t.targetid, t.amount, t.type, s.balance, s.userid, s.isblocked
    FROM transfer t
    JOIN savingaccount s ON s.accountid = t.sourceid
    WHERE t.sourceid BETWEEN 1 AND 1500000
      AND t.amount > 0
) bonect_memory_v79_join_transfer_sa_wide;

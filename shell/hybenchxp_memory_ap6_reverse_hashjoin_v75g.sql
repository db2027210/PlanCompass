-- v75g reverse-Memory switch candidate (AP-6 alt): Hash Join heavy.
-- Default: Hash Join builds large hash on transfer slice (high memory).
-- Candidate: raise cpu_tuple_cost / random_page_cost or lower seq_page_cost to prefer
-- Nested Loop / Merge-ish paths that shrink or remove Hash node.
SELECT count(*)
FROM (
    SELECT t.sourceid, t.amount, s.balance
    FROM transfer t
    JOIN savingaccount s ON s.accountid = t.sourceid
    WHERE t.sourceid BETWEEN 1 AND 400000
      AND t.amount > 0
) bonect_memory_v75g_ap6_reverse_hashjoin;

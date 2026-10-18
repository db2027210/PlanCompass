-- v79 Bitmap+Join hybrid: join with selective filter that can flip Bitmap Heap under cost params.
SELECT count(*)
FROM (
    SELECT t.sourceid, t.amount, s.balance
    FROM transfer t
    JOIN savingaccount s ON s.accountid = t.sourceid
    WHERE t.type = 'salary'
      AND t.sourceid BETWEEN 1 AND 900000
      AND t.amount > 50
) bonect_memory_v79_bitmap_transfer_sa_join;

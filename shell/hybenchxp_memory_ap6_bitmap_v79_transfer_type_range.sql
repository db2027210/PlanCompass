-- v79 Bitmap hybrid candidate: selective type + sourceid range for Bitmap Heap + optional HashAgg.
SELECT count(*)
FROM (
    SELECT t.sourceid, count(*) AS n, sum(t.amount) AS amt
    FROM transfer t
    WHERE t.type = 'salary'
      AND t.sourceid BETWEEN 1 AND 2000000
      AND t.amount > 0
    GROUP BY t.sourceid
) bonect_memory_v79_bitmap_transfer_type_range;

-- v78: sourceid + targetid bucket (mod 32) — extra real key from targetid.
SELECT count(*)
FROM (
    SELECT
        t.sourceid,
        (t.targetid % 32) AS tgt_bucket,
        count(*) AS row_count,
        sum(t.amount) AS total_amount,
        avg(t.amount) AS avg_amount
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 5000000
    GROUP BY t.sourceid, (t.targetid % 32)
) bonect_memory_v78_src_tgtbucket;

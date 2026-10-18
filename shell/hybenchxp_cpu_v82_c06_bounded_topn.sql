-- v82 C06: bounded ordered transfer result.
-- Default uses Parallel Seq Scan + full Sort; rpc=1 can use ordered index access
-- with Incremental Sort. The amount predicate is highly selective but realistic.
SELECT id, sourceid, targetid, amount
FROM transfer
WHERE amount > 19.99
ORDER BY sourceid, amount DESC, id
LIMIT 5000;

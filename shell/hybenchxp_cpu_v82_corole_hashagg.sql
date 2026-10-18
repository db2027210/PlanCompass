-- v82 same-vector non-target co-role. Mapping only; never a primary CPU speed query.
SELECT count(*)
FROM (
  SELECT t.sourceid,
         count(*) AS row_count,
         round(sum(t.amount)::numeric, 2) AS total_amount
  FROM transfer t
  WHERE t.sourceid BETWEEN 2400001 AND 2700000
  GROUP BY t.sourceid
  ORDER BY total_amount DESC, row_count DESC, t.sourceid
  LIMIT 300000
) AS q;
